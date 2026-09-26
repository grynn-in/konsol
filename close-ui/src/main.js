// konsol#305 B16: wires the router (route.js's `/close/<year>/<period>/
// <screen>`, D5) into the app entry. App.vue (B17) mounts AppShell, whose
// <router-view/> this router drives. A bare `/close` asks A15 for the
// landing period and lands on the persona's first screen (B16c), replacing
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
