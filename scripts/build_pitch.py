"""Build the self-contained pitch from the pinned Swiss template and local assets."""
import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'frontend/pitch'
OUTPUT = ROOT / 'frontend/public/assets/pitch/index.html'


def build():
    html = (SOURCE / 'template-swiss.html').read_text()
    slides = (SOURCE / 'slides.html').read_text()
    for name in ('home', 'discovery', 'assistant', 'map', 'media'):
        screenshot = base64.b64encode((SOURCE / f'images/user-{name}.png').read_bytes()).decode()
        slides = slides.replace('{{' + name.upper() + '_IMAGE}}', 'data:image/png;base64,' + screenshot)
    assert not re.search(r'\{\{[A-Z_]+\}\}', slides), 'Unresolved image placeholder'
    slide_ids = re.findall(r'<section[^>]*data-slide-id="([^"]+)"', slides)
    slide_count = len(slide_ids)
    for number, block in enumerate(re.findall(r'<section class="slide\b[\s\S]*?</section>', slides), 1):
        numbered = re.sub(r'\b\d{2} / \d{2}\b', f'{number:02d} / {slide_count}', block)
        slides = slides.replace(block, numbered, 1)
    html = re.sub(r'<title>.*?</title>', '<title>汴梁游记 · 城市旅游智能体路演</title>', html)
    html = re.sub(r'<link\b[^>]*>', '', html)
    html = re.sub(r'(<div id="deck">)[\s\S]*?(</div>\s*<div id="nav">)',
                  lambda m: m[1] + '\n' + slides + '\n' + m[2], html, count=1)
    notes = json.loads((SOURCE / 'notes.json').read_text())
    html = re.sub(r'const SPEAKER_NOTES = \[[\s\S]*?\];\s*window.__SPEAKER_NOTES__',
                  lambda _: 'const SPEAKER_NOTES = ' + json.dumps(notes, ensure_ascii=False, indent=2)
                  + ';\nwindow.__SPEAKER_NOTES__', html, count=1)
    html = html.replace('</head>', '<style>\n' + (SOURCE / 'pitch.css').read_text() + '\n</style>\n</head>')
    html = re.sub(r'<script src="https://unpkg.com/lucide[^>]*></script>\s*<script>lucide.createIcons\(\);</script>', '', html)
    motion = re.sub(r'//# sourceMappingURL=.*', '', (SOURCE / 'motion.min.js').read_text())
    motion_url = 'data:text/javascript;base64,' + base64.b64encode(motion.encode()).decode()
    html = re.sub(r'let motion;\s*try \{[\s\S]*?\nif\(motion\)\{',
                  lambda _: 'const motion = await import(' + json.dumps(motion_url) + ');\nif(motion){', html, count=1)
    # Keep hidden slides out of keyboard navigation and the accessibility tree.
    html = html.replace('darkMode = isDark;', '''darkMode = isDark;
  slides.forEach((slide,i)=>{slide.inert=i!==idx;slide.setAttribute('aria-hidden',String(i!==idx));});
  dispatchEvent(new CustomEvent('pitch-slide-change',{detail:{index:idx}}));''')
    html = html.replace("if(overviewOn)return;\n  if(e.key===", """if(overviewOn)return;
  if(e.target instanceof Element){
    if(e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
    if(e.target.closest('[data-interactive]') && !['PageDown','PageUp'].includes(e.key))return;
    if([' ','Enter'].includes(e.key) && e.target.closest('button,a'))return;
  }
  if(['ArrowRight','ArrowLeft','ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].includes(e.key))e.preventDefault();
  if(e.key===""")
    html = html.replace("addEventListener('wheel',e=>{\n  wheelAcc", """addEventListener('wheel',e=>{
  if(innerWidth<=900 || (e.target instanceof Element && e.target.closest('[data-interactive]')))return;
  wheelAcc""")
    html = html.replace("addEventListener('touchstart',e=>{tx=", "addEventListener('touchstart',e=>{if(e.target instanceof Element && e.target.closest('[data-interactive]')){tx=NaN;return;}tx=")
    html = html.replace('const dx=(e.changedTouches[0].clientX-tx);', 'if(!Number.isFinite(tx))return;\n  const dx=(e.changedTouches[0].clientX-tx);')
    html = html.replace('window.__pptPresenter?.onSlideChange(idx);', 'window.__pptPresenter?.onSlideChange(idx);\n  window.__pitchRenderPresenter?.();')
    html = html.replace("addEventListener('keydown',e=>{", "addEventListener('keydown',e=>{\n  if(document.querySelector('dialog[open]'))return;")
    html = html.replace('go(Number.isFinite(initialSlide) ? initialSlide : 0);',
                        'go(Number.isFinite(initialSlide) ? initialSlide : 0,{force:true});')
    html = html.replace('const clone=s.cloneNode(true);', '''const clone=s.cloneNode(true);
    clone.inert=true;clone.setAttribute('aria-hidden','true');
    clone.removeAttribute('id');clone.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));''')
    html = html.replace('</body>', '<script>\n' + (SOURCE / 'demo.js').read_text() + '\n</script>\n</body>')
    assert slide_count == len(notes) == 15
    assert slide_ids == [note['id'] for note in notes]
    assert '[必填]' not in html
    assert not re.search(r'<(?:script|link)\b[^>]*(?:src|href)="https?://', html)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html)
    print(f'{OUTPUT}: {slide_count} slides, {OUTPUT.stat().st_size:,} bytes, assets embedded')


if __name__ == '__main__':
    build()
