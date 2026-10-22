import { MediaQuery } from 'svelte/reactivity'

/** ≤700px is the phone layout; a phone turned sideways gets the laptop layout. */
export const phone = new MediaQuery('max-width: 700px', false)
