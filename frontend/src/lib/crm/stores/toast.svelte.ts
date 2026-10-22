/**
 * One toast at a time: bottom centre (top on phone), for 5 seconds. After a write it offers
 * Undo; after a failed refresh it offers Retry.
 */
interface ToastAction {
	label: string
	run: () => void
}

class ToastStore {
	message = $state('')
	visible = $state(false)
	tone = $state<'default' | 'error'>('default')
	action = $state<ToastAction | null>(null)
	#timer: ReturnType<typeof setTimeout> | undefined

	show(
		message: string,
		opts: { undo?: () => void; action?: ToastAction; tone?: 'default' | 'error' } = {}
	) {
		this.message = message
		this.tone = opts.tone ?? 'default'
		this.action = opts.undo ? { label: 'Undo', run: opts.undo } : (opts.action ?? null)
		this.visible = true
		clearTimeout(this.#timer)
		this.#timer = setTimeout(() => this.hide(), 5000)
	}

	hide() {
		clearTimeout(this.#timer)
		this.visible = false
		this.action = null
	}

	runAction() {
		const fn = this.action?.run
		this.hide()
		fn?.()
	}
}

export const toast = new ToastStore()
