"""Short, in-memory WAV transcription through Alibaba Cloud DashScope."""
import asyncio
import base64
import io
import os
import wave
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter(prefix='/api/speech', tags=['speech'])
MODEL = 'qwen-audio-3.0-asr-flash'
MAX_SECONDS = 60
MAX_BYTES = 2 * 1024 * 1024
slots = asyncio.Semaphore(2)
HOTWORDS = {word: 3 for word in ('开封', '万岁山武侠城', '清明上河园', '王婆说媒', '龙亭', '铁塔', '开封府')}


def configuration():
    key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    base = os.getenv('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/api/v1').strip().rstrip('/')
    try:
        parts = urlsplit(base)
        host = parts.hostname or ''
        allowed = host in {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com'} or host.endswith(('.cn-beijing.maas.aliyuncs.com', '.ap-southeast-1.maas.aliyuncs.com'))
        valid = allowed and parts.scheme == 'https' and not (parts.username or parts.password or parts.query or parts.fragment) and parts.path == '/api/v1' and parts.port in (None, 443)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, detail='语音服务配置有误，请联系维护者')
    return key, base


@router.get('/status')
def status():
    key, _ = configuration()
    return {'available': bool(key), 'max_seconds': MAX_SECONDS}


def validate_wav(content: bytes):
    if len(content) > MAX_BYTES:
        raise HTTPException(413, detail='录音文件过大，请控制在60秒内')
    try:
        with wave.open(io.BytesIO(content), 'rb') as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000 or audio.getcomptype() != 'NONE':
                raise HTTPException(415, detail='请使用16kHz、单声道的16位WAV录音')
            frames = audio.getnframes()
            if not 3200 <= frames <= 16000 * MAX_SECONDS:
                raise HTTPException(422, detail='请录制0.2至60秒的语音')
            if len(audio.readframes(frames)) != frames * 2:
                raise HTTPException(422, detail='录音文件不完整，请重新录制')
    except (wave.Error, EOFError, ValueError):
        raise HTTPException(415, detail='无法读取录音，请重新录制') from None


async def transcribe(content: bytes, key: str, base: str):
    payload = {
        'model': MODEL,
        'input': {'messages': [{'role': 'user', 'content': [{
            'type': 'input_audio',
            'input_audio': {'data': 'data:audio/wav;base64,' + base64.b64encode(content).decode('ascii')},
        }]}]},
        'parameters': {'format': 'wav', 'sample_rate': '16000', 'vocabulary': HOTWORDS},
    }
    try:
        # One call only: avoid duplicate billing through automatic retries.
        async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=8), follow_redirects=False) as client:
            response = await client.post(
                base + '/services/aigc/multimodal-generation/generation', json=payload,
                headers={'Authorization': 'Bearer ' + key, 'X-DashScope-SSE': 'disable'},
            )
    except httpx.TimeoutException:
        raise HTTPException(504, detail='语音识别超时，请稍后重试') from None
    except httpx.RequestError:
        raise HTTPException(502, detail='暂时无法连接语音服务，请稍后重试') from None
    if response.status_code in (401, 403):
        raise HTTPException(503, detail='语音服务暂不可用，请联系维护者检查授权')
    if response.status_code == 429:
        raise HTTPException(429, detail='语音服务繁忙，请稍后重试')
    if response.status_code >= 400:
        raise HTTPException(502, detail='语音识别失败，请稍后重试')
    try:
        body = response.json()
        if not isinstance(body, dict) or body.get('code'):
            raise ValueError
        output = body['output']
        if not isinstance(output, dict):
            raise ValueError
        text = output.get('text')
        if text is None:
            text = output['output']['sentence']['text']
        if not isinstance(text, str):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(502, detail='语音服务返回异常，请稍后重试') from None
    if not text.strip():
        raise HTTPException(422, detail='没有听清语音，请靠近麦克风重新录制')
    return {'text': text.strip()}


@router.post('/transcriptions')
async def upload(file: UploadFile = File(...)):
    try:
        key, base = configuration()
        if not key:
            raise HTTPException(503, detail='语音输入暂未开通，请稍后再试')
        content = await file.read(MAX_BYTES + 1)
    finally:
        await file.close()
    validate_wav(content)
    if slots.locked():
        raise HTTPException(429, detail='语音服务繁忙，请稍后重试')
    async with slots:
        return JSONResponse(await transcribe(content, key, base), headers={'Cache-Control': 'no-store'})
