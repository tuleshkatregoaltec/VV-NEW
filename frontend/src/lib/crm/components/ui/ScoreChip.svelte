<script lang="ts">
import { scoreClass } from '../../config'

let { score }: { score: number } = $props()
</script>

<span class={['score', scoreClass(score)]} title="Score" aria-label="Score {score}">{score}</span>
