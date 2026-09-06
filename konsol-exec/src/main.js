import { createApp } from "vue";
import { FrappeUIProvider } from "frappe-ui";
import App from "./App.vue";
import { router } from "./router.js";
import "./index.css";

const app = createApp(App);
app.use(router);
app.component("FrappeUIProvider", FrappeUIProvider);
app.mount("#root");
