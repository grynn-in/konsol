// konsol#305 B16: wires the router (route.js's `/close/<year>/<period>/
// <screen>`, D5) into the app entry. App.vue (B03) has no <router-view/>
// yet — that lands with AppShell in B17 — so for now this only drives the
// URL: a bare `/close` still asks A15 for the landing period and replaces
// it (router.js's `beforeEach` guard), even before anything routed renders.
import { createApp } from "vue";
import { FrappeUIProvider } from "frappe-ui";
import App from "./App.vue";
import { createCloseRouter } from "./router.js";
import "./index.css";

const app = createApp(App);
app.component("FrappeUIProvider", FrappeUIProvider);
app.use(createCloseRouter());
app.mount("#root");
