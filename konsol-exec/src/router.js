import { createRouter, createWebHistory } from "vue-router";
import CloseChecklist from "./components/CloseChecklist.vue";
import StepDetail from "./components/StepDetail.vue";
import MonthView from "./components/MonthView.vue";
import UploadView from "./components/UploadView.vue";
import NoPeriodHome from "./components/NoPeriodHome.vue";
import { currentMonthPath } from "./home.js";
import { periodTree } from "./homeApi.js";

/**
 * The period is the location: /konsol-exec/2026/9 is P09 of FY2026. A link
 * pasted into chat during a close carries the period with it, and the
 * navigator and the title-bar path both read it from here.
 *
 * "/" is the no-period home: the navigator alone, nothing selected. Anything
 * unmatched lands there too. A guard below tries to move it on to the
 * server's declared current period — never a guessed calendar month
 * (konsol#189 review finding 2) — and simply stays put when there isn't one
 * or the server can't be reached.
 *
 * The close steps keep their routes (/close/:step[/:tab]); the lane opens them
 * for the month the shell has selected.
 */
const routes = [
	{ path: "/", name: "home", component: NoPeriodHome },
	{ path: "/:year(\\d{4})/:period(\\d{1,3})", name: "month", component: MonthView, props: true },
	{ path: "/uploads", name: "uploads", component: UploadView },
	{ path: "/close", name: "close", component: CloseChecklist },
	{ path: "/close/:step", name: "step", component: StepDetail, props: true },
	{ path: "/close/:step/:tab", name: "step-tab", component: StepDetail, props: true },
	{ path: "/:pathMatch(.*)*", redirect: "/" },
];

export const router = createRouter({
	history: createWebHistory("/konsol-exec/"),
	routes,
	scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
	if (to.path !== "/") return true;
	let tree;
	try {
		tree = await periodTree();
	} catch {
		return true; // can't reach the server: stay on the no-period home
	}
	const target = currentMonthPath(tree);
	return target === "/" ? true : target;
});
