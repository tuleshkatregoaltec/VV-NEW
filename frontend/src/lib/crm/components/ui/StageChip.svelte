<script lang="ts">
import type { LeadStage } from '../../api/types'
import { stageOf } from '../../config'
import Chip from './Chip.svelte'

let { stage }: { stage: LeadStage } = $props()
const s = $derived(stageOf(stage))
</script>

<Chip tone={s.tone}>{s.label}</Chip>
