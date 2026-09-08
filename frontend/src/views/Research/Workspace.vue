<template>
  <section class="research-workspace-shell">
    <el-button text @click="router.push('/research')">
      <el-icon><ArrowLeft /></el-icon>
      返回研究目录
    </el-button>
    <h1>{{ code }}</h1>
    <p>{{ marketLabel }}研究工作区</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const code = computed(() => String(route.params.code || ''))
const marketLabel = computed(
  () => ({ CN: 'A 股', HK: '港股', US: '美股' })[String(route.query.market)] || ''
)
</script>

<style scoped>
.research-workspace-shell h1 {
  margin: 24px 0 6px;
  font-size: 24px;
  letter-spacing: 0;
}

.research-workspace-shell p {
  margin: 0;
  color: var(--el-text-color-secondary);
}
</style>
