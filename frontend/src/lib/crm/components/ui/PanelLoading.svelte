<script lang="ts">
import type { CreateQueryResult } from '@tanstack/svelte-query'
import { errorText } from '../../write'
import ErrorLine from './ErrorLine.svelte'
import Panel from './Panel.svelte'
import Skeleton from './Skeleton.svelte'

/** Stand-in panel while a record loads, or when it fails. */
let { query, kicker = '' }: { query: CreateQueryResult<unknown, Error>; kicker?: string } = $props()
</script>

<Panel {kicker} title={query.isError ? 'Could not open this record' : 'Loading…'}>
	{#if query.isError}
		<ErrorLine message={errorText(query.error)} onretry={() => query.refetch()} />
	{:else}
		<Skeleton rows={5} />
	{/if}
</Panel>
