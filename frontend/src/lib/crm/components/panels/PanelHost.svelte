<script lang="ts">
import type { PanelState } from '../../stores/panel.svelte'
import AddRecordPanel from './AddRecordPanel.svelte'
import AreasPanel from './AreasPanel.svelte'
import BrochurePanel from './BrochurePanel.svelte'
import ClientPanel from './ClientPanel.svelte'
import DayPanel from './DayPanel.svelte'
import DealPanel from './DealPanel.svelte'
import EventPanel from './EventPanel.svelte'
import ImportPanel from './ImportPanel.svelte'
import LeadPanel from './LeadPanel.svelte'
import ListingPanel from './ListingPanel.svelte'
import MarketPanel from './MarketPanel.svelte'
import NewDealPanel from './NewDealPanel.svelte'
import NewLeadPanel from './NewLeadPanel.svelte'
import NewListingPanel from './NewListingPanel.svelte'
import NewMenuPanel from './NewMenuPanel.svelte'
import NewOwnerPanel from './NewOwnerPanel.svelte'
import NewTaskPanel from './NewTaskPanel.svelte'
import NotificationsPanel from './NotificationsPanel.svelte'
import OwnerLeadPanel from './OwnerLeadPanel.svelte'
import OwnerPanel from './OwnerPanel.svelte'
import OwnerReportPanel from './OwnerReportPanel.svelte'
import RenewalPanel from './RenewalPanel.svelte'
import SearchPanel from './SearchPanel.svelte'
import SendPanel from './SendPanel.svelte'
import WhichDocPanel from './WhichDocPanel.svelte'

let { state: p }: { state: PanelState } = $props()

/** Remount when the record changes, not when only the tab changes. */
const key = $derived.by(() => {
	switch (p.kind) {
		case 'lead':
		case 'deal':
		case 'listing':
		case 'owner':
		case 'market':
		case 'renewal':
		case 'event':
		case 'brochure':
		case 'owner-report':
			return `${p.kind}:${p.id}`
		case 'owner-lead':
			return `owner-lead:${p.id}`
		case 'add-record':
			return `add-record:${p.ownerId}`
		case 'which-doc':
			return `which-doc:${p.dealId}:${p.index}`
		case 'send':
			return `send:${p.target}:${p.id}`
		case 'client':
			return `client:${p.client}`
		case 'day':
			return `day:${p.date}`
		default:
			return p.kind
	}
})
</script>

{#key key}
	{#if p.kind === 'lead'}
		<LeadPanel id={p.id} tab={p.tab} />
	{:else if p.kind === 'deal'}
		<DealPanel id={p.id} tab={p.tab} />
	{:else if p.kind === 'listing'}
		<ListingPanel id={p.id} />
	{:else if p.kind === 'owner'}
		<OwnerPanel id={p.id} unit={p.unit} />
	{:else if p.kind === 'market'}
		<MarketPanel id={p.id} />
	{:else if p.kind === 'client'}
		<ClientPanel client={p.client} />
	{:else if p.kind === 'renewal'}
		<RenewalPanel id={p.id} />
	{:else if p.kind === 'event'}
		<EventPanel id={p.id} />
	{:else if p.kind === 'day'}
		<DayPanel date={p.date} />
	{:else if p.kind === 'search'}
		<SearchPanel />
	{:else if p.kind === 'notifications'}
		<NotificationsPanel />
	{:else if p.kind === 'new'}
		<NewMenuPanel />
	{:else if p.kind === 'new-lead'}
		<NewLeadPanel leadType={p.leadType} />
	{:else if p.kind === 'new-listing'}
		<NewListingPanel fromLeadId={p.fromLeadId} />
	{:else if p.kind === 'new-deal'}
		<NewDealPanel leadId={p.leadId} />
	{:else if p.kind === 'new-task'}
		<NewTaskPanel date={p.date} />
	{:else if p.kind === 'new-owner'}
		<NewOwnerPanel />
	{:else if p.kind === 'new-import'}
		<ImportPanel ownersOnly={p.ownersOnly} />
	{:else if p.kind === 'areas'}
		<AreasPanel />
	{:else if p.kind === 'add-record'}
		<AddRecordPanel ownerId={p.ownerId} />
	{:else if p.kind === 'owner-lead'}
		<OwnerLeadPanel id={p.id} />
	{:else if p.kind === 'send'}
		<SendPanel target={p.target} id={p.id} />
	{:else if p.kind === 'brochure'}
		<BrochurePanel id={p.id} />
	{:else if p.kind === 'owner-report'}
		<OwnerReportPanel id={p.id} />
	{:else if p.kind === 'which-doc'}
		<WhichDocPanel dealId={p.dealId} files={p.files} index={p.index} />
	{/if}
{/key}
