<script setup>
/**
 * konsol#305 B17: the app root. FrappeUIProvider around AppShell, which holds
 * the header, the role-filtered nav and the <router-view/> for the router
 * main.js installs (B16).
 */
import AppShell from "./components/AppShell.vue";
</script>

<template>
	<FrappeUIProvider>
		<AppShell />
	</FrappeUIProvider>
</template>
