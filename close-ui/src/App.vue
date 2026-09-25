<script setup>
/**
 * konsol#305 B03: minimal entry shell.
 *
 * The router lands in B16 and the real workspace (title bar, navigator,
 * screens) lands with the rows that follow it. Until then this is a static
 * placeholder that proves FrappeUIProvider mounts and the Vite build
 * succeeds — nothing here chooses a period or a screen.
 */
</script>

<template>
	<FrappeUIProvider>
		<div class="p-4">konsol close</div>
	</FrappeUIProvider>
</template>
