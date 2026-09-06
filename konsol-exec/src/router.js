import { createRouter, createWebHistory } from "vue-router";
import CloseChecklist from "./components/CloseChecklist.vue";
import StepDetail from "./components/StepDetail.vue";

/**
 * Close-first routes. The period is in the path, so a link pasted into chat
 * during a close carries the period with it — the old app's deep links lost it,
 * because the period lived inside a form.
 */
const routes = [
	{ path: "/", redirect: "/close" },
	{ path: "/close", name: "close", component: CloseChecklist },
	{ path: "/close/:step", name: "step", component: StepDetail, props: true },
	{ path: "/close/:step/:tab", name: "step-tab", component: StepDetail, props: true },
	{ path: "/:pathMatch(.*)*", redirect: "/close" },
];

export const router = createRouter({
	history: createWebHistory("/konsol-exec/"),
	routes,
	scrollBehavior: () => ({ top: 0 }),
});
