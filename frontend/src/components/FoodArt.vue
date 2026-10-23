<script setup lang="ts">
defineProps<{name:string}>()
</script>
<template>
 <svg class="food-art" viewBox="0 0 560 320" role="img" :aria-label="name+'主题插画，非实景照片'">
  <rect width="560" height="320" :fill="name.includes('包')?'#eee5d4':'#e8dfd2'"/>
  <path d="M0 255Q200 190 560 242V320H0Z" fill="#d9c7aa" opacity=".45"/>
  <path d="m435 52-30 211m47-207-30 211" stroke="#8e684a" stroke-width="7" stroke-linecap="round"/>
  <circle cx="105" cy="90" r="30" fill="#d4d9bc"/><circle cx="105" cy="90" r="23" fill="#9da982"/><path d="M93 85q12-8 24 1" fill="none" stroke="#e5e6c8" stroke-width="3"/>
  <ellipse cx="274" cy="245" rx="144" ry="24" fill="#a8997c" opacity=".2"/>
  <template v-if="name.includes('包')">
   <ellipse cx="273" cy="186" rx="139" ry="80" fill="#bb8d59"/><ellipse cx="273" cy="175" rx="139" ry="80" fill="#dac098" stroke="#c09e71" stroke-width="5"/><ellipse cx="273" cy="175" rx="122" ry="65" fill="#eee0bd"/>
   <g fill="none" stroke="#cfbc91" stroke-width="2" opacity=".5"><path d="M174 146h195m-208 20h225m-222 20h222m-203 20h188"/></g>
   <g v-for="(position,i) in [[220,143],[304,140],[185,192],[270,192],[350,187]]" :key="i" :transform="`translate(${position[0]},${position[1]})`">
    <path d="M-34 4Q-34-17-13-25L0-34 14-24Q37-14 35 7C33 32-34 33-34 4" fill="#fff4d9" stroke="#d9bf8f" stroke-width="2"/>
    <g fill="none" stroke="#d8c09c" stroke-width="1.8"><path d="M0-29q-20 15-19 34M0-29Q-7-9-6 9M0-29Q8-10 8 9M0-29Q24-10 23 5"/><path d="M-6-30q5-7 12 0"/></g>
   </g>
  </template>
  <template v-else>
   <ellipse cx="272" cy="192" rx="143" ry="77" fill="#bdc3a6"/><ellipse cx="272" cy="182" rx="143" ry="77" fill="#faf4e5" stroke="#d1cfb7" stroke-width="4"/><ellipse cx="272" cy="182" rx="117" ry="57" fill="#ece4cd"/>
   <g fill="#b9804e" stroke="#95633d" stroke-width="2"><path d="m186 160 80-34 43 24-81 37Z"/><path d="m183 177 84-35 44 24-83 38Z"/><path d="m184 194 84-36 43 24-82 40Z"/><path d="m244 173 74-30 37 24-75 34Z"/><path d="m246 190 74-31 37 24-75 35Z"/><path d="m247 207 74-32 38 24-76 36Z"/></g>
   <g fill="none" stroke="#e9bd85" stroke-width="6"><path d="m194 160 71-29m-71 46 71-29m-70 46 71-29m-10 7 60-25m-59 42 60-25m-58 42 60-25"/></g>
   <g fill="#72895d"><ellipse cx="171" cy="204" rx="12" ry="6" transform="rotate(-35 171 204)"/><ellipse cx="164" cy="190" rx="12" ry="6" transform="rotate(35 164 190)"/><ellipse cx="372" cy="187" rx="12" ry="6" transform="rotate(-35 372 187)"/></g>
  </template>
 </svg>
</template>
