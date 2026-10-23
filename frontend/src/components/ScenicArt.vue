<script setup lang="ts">
withDefaults(defineProps<{theme?:string;hero?:boolean}>(),{theme:'song',hero:false})
</script>
<template>
<svg class="scenic-art" viewBox="0 0 700 400" role="img" :aria-label="hero?'汴梁园林原创示意插画':'景区主题原创插画，非实景照片'">
 <rect width="700" height="400" :fill="theme==='wuxia'?'#e4e9de':theme==='tower'?'#ede9df':'#e2ece6'"/>
 <circle cx="555" cy="86" r="44" fill="#f8e5be"/>
 <path d="M0 220 Q130 135 270 212 T700 185 V400 H0Z" fill="#cedbd0"/>
 <path d="M0 277 Q150 223 300 262 T700 238 V400 H0Z" fill="#c0d5c8"/>
 <path d="M-20 355 Q150 260 285 320 T740 300 V400 H0Z" fill="#a4c4b8"/>
 <g opacity=".42" fill="none" stroke="#f2f6ed" stroke-width="3"><path d="M70 355h110m50 20h120m145-49h140M30 390h190m225-33h150"/></g>
 <g v-if="theme==='tower'" fill="#7b8975" stroke="#536754" stroke-width="3">
  <path d="M290 302h120l-15-32h-90Z"/><path d="M300 264h100l-12-31h-76Z"/><path d="M309 227h82l-10-30h-62Z"/><path d="M316 191h68l-9-28h-50Z"/><path d="M324 157h52l-8-26h-36Z"/><path d="M330 125h40l-20-32Z"/><path d="M350 95V76"/><path d="M311 301V272m78 29v-29m-66-9v-30m54 30v-30m-43-7v-28m32 28v-28"/>
 </g>
 <g v-else>
 <path d="M260 310 Q365 185 470 310" fill="#c3b896" stroke="#85927c" stroke-width="8"/>
 <path d="M285 312 Q365 231 446 312" fill="#bad0c2"/>
 <path d="M250 305 Q365 172 480 305" fill="none" stroke="#e8e5cf" stroke-width="11"/>
 <g stroke="#728570" stroke-width="5"><path d="M266 287v-28m24-3v-28m26 7v-29m28 18v-29m30 29v-29m27 41v-30m26 49v-29m25 54v-29"/></g>
 <g fill="#d7c9a4" stroke="#78856e" stroke-width="4"><path d="M94 270V176h119v94Z"/><path d="M85 175Q113 169 155 140Q185 167 225 175Z" fill="#687d69"/><path d="M106 146Q137 140 155 115Q175 139 202 146Z" fill="#6d816e"/><path d="M153 118V98"/><path d="M116 270v-66h25v66m28 0v-66h25v66" fill="#a5997d"/></g>
 <g fill="#d9cdae" stroke="#738771" stroke-width="4"><path d="M508 249v-94h105v94Z"/><path d="M493 156Q528 148 561 121Q590 148 630 156Z" fill="#6e836f"/><path d="M520 249v-65h28v65m24 0v-65h28v65" fill="#a79e80"/><path d="M561 124v-22"/></g>
 </g>
 <g stroke="#6d846b" stroke-width="7" fill="#9db19a"><path d="M47 302V192m0 50-20-35m20 55 25-43"/><circle cx="45" cy="186" r="35"/><circle cx="20" cy="214" r="29"/><circle cx="72" cy="218" r="31"/><path d="M657 280V177m0 50-18-25"/><circle cx="656" cy="170" r="32"/><circle cx="680" cy="200" r="29"/></g>
 <path d="M0 332q85-25 145-10m385-40q91-29 170 5" fill="none" stroke="#70886e" stroke-width="7"/>
 <g fill="#f3f1dc"><ellipse cx="195" cy="355" rx="10" ry="3"/><ellipse cx="500" cy="366" rx="13" ry="3"/></g>
</svg>
</template>
