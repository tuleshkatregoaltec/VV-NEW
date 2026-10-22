import { redirect } from '@sveltejs/kit'

/** v2 (7 Oct): the Connections page is gone; the Inbox's Sources card shows the same status. */
export function load() {
	redirect(307, '/crm/inbox')
}
