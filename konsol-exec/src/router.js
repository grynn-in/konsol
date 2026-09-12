import { createRouter, createWebHistory } from "vue-router";
import CloseChecklist from "./components/CloseChecklist.vue";
import StepDetail from "./components/StepDetail.vue";
import MonthView from "./components/MonthView.vue";
import { currentMonthPath } from "./home.js";

/**
 * The period is the location: /konsol-exec/2026/9 is P09 of FY2026. A link
 * pasted into chat during a close carries the period with it, and the
 * navigator and the title-bar path both read it from here.
 *
 * The close steps keep their routes (/close/:step[/:tab]); the lane opens them
 * for the month the shell has selected.
 */
const routes = [
	{ path: "/", redirect: () => currentMonthPath() },
	{ path: "/:year(\\d{4})/:period(\\d{1,2})", name: "month", component: MonthView, props: true },
	{ path: "/close", name: "close", component: CloseChecklist },
	{ path: "/close/:step", name: "step", component: StepDetail, props: true },
	{ path: "/close/:step/:tab", name: "step-tab", component: StepDetail, props: true },
	{ path: "/:pathMatch(.*)*", redirect: () => currentMonthPath() },
];

export const router = createRouter({
	history: createWebHistory("/konsol-exec/"),
	routes,
	scrollBehavior: () => ({ top: 0 }),
});
