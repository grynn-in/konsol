// konsol#305 B16: router.js
//
// Maps /close/:year/:period/:screen to close-ui/src/screens/<Screen>.vue
// through import.meta.glob, so a screen row (B18-B24) only has to add its
// file — this router never changes for it. The screen file name is the
// PascalCase of the URL slug ("my-work" -> "MyWork.vue", per B07's SCREENS).
// A slug with no matching file renders a visible "Not built yet" state,
// never a blank screen or a silent 404.
//
// D5: the period lives in the URL and nothing is remembered client-side, so
// `/close` with nothing after it (route.js (B07) parses this as
// `{year: null}`) has no client default. `createCloseRouter` installs a
// `beforeEach` guard that asks A15 (`period_api.get_context`) for the
// landing and replaces the URL with `landingPath(result)`.
//
// `createRouter`/`createWebHistory` need `window.history`, and
// `import.meta.glob` is a Vite build-time macro — neither exists under
// plain `node --test`. Both stay inside `createCloseRouter`, called only
// from main.js at app start, so the test can import the pure `landingPath`
// helper alone (mirrors route.test.mjs (B07) keeping vue-router itself out
// of the pure test).
import { createRouter, createWebHistory } from "vue-router";
import { defineComponent, defineAsyncComponent, computed, h, reactive } from "vue";

/** Why /close has no period to land on, or the error reaching the server (B16b); the shell renders it. */
export const landingState = reactive({ reason: null, error: null });
import { get } from "./api.js";
import { format } from "./route.js";

function pascalCase(slug) {
  return String(slug)
    .split("-")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join("");
}

const NotBuiltYet = defineComponent({
  name: "NotBuiltYet",
  render() {
    return h("div", { class: "p-4 text-gray-600" }, "Not built yet.");
  },
});

/**
 * `get_context()` result -> the D5 landing path, on the My work screen.
 * `landing.period` null names the gap instead of guessing a period — an
 * undeclared first close, or a Viewer who has signed nothing (A15's
 * `landing.reason`): `/close/none/<reason>/my-work`, the reason
 * URL-escaped since it can be a full sentence, not a short code.
 */
export function landingPath(context) {
  const landing = context.landing;
  if (landing.period == null) {
    // B16b: no period to land on (first close undeclared, or a Viewer with
    // nothing signed). Stay on /close; the shell shows `landing.reason`.
    // Putting the reason into the URL made route.js read it as a bad year.
    return null;
  }
  const [year, period] = landing.period;
  return format({ year, period, screen: "my-work" });
}

/**
 * Builds the app's vue-router instance. Deferred behind this function (never
 * called at module load, and never called by the test) because it calls
 * `createWebHistory` and `import.meta.glob` — see the file header.
 */
export function createCloseRouter() {
  const screenModules = import.meta.glob("./screens/*.vue");

  function screenComponent(slug) {
    const loader = screenModules[`./screens/${pascalCase(slug)}.vue`];
    return loader ? defineAsyncComponent(loader) : NotBuiltYet;
  }

  const ScreenLoader = defineComponent({
    name: "ScreenLoader",
    props: { screen: { type: String, required: true } },
    setup(props) {
      const Comp = computed(() => screenComponent(props.screen));
      return () => h(Comp.value);
    },
  });

  const router = createRouter({
    history: createWebHistory("/close"),
    routes: [
      { path: "/", component: NotBuiltYet },
      { path: "/:year/:period/:screen", component: ScreenLoader, props: true },
    ],
  });

  router.beforeEach(async (to) => {
    if (to.path !== "/") return true;
    landingState.reason = null;
    landingState.error = null;
    let context;
    try {
      context = await get("konsol.close.period_api.get_context");
    } catch (e) {
      // B16b: never a blank screen: the shell shows the server's message.
      landingState.error = e.message;
      return true;
    }
    const target = landingPath(context);
    if (target == null) {
      landingState.reason = context.landing.reason;
      return true;
    }
    return target.replace(/^\/close/, "");
  });

  return router;
}
