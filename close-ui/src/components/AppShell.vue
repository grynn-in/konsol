<script setup>
/**
 * konsol#305 B16: AppShell — a minimal <router-view/> wrapper for now.
 *
 * The real shell (header, period picker, freshness bar, role-filtered nav,
 * load states) lands in B17. This file exists now so the router (and B17)
 * have a stable import path; it renders whatever the router matched and
 * nothing else.
 */
</script>

<template>
	<router-view />
</template>
