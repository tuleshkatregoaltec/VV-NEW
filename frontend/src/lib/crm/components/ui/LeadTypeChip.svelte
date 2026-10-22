<script lang="ts">
import type { LeadType } from '../../api/types'
import { LEAD_TYPES } from '../../config'
import Chip from './Chip.svelte'

let { type }: { type: LeadType } = $props()
</script>

<Chip tone={LEAD_TYPES[type].tone}>{LEAD_TYPES[type].label}</Chip>
