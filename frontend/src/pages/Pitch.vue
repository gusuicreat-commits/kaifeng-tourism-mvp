<script setup lang="ts">
import {onMounted} from 'vue'

const destination='/assets/pitch/index.html'+window.location.search+window.location.hash
onMounted(()=>window.location.replace(destination))
</script>

<template>
 <a :href="destination">打开汴梁游记路演</a>
</template>
