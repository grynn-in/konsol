import { createApp } from "vue";
import { FrappeUIProvider } from "frappe-ui";
import App from "./App.vue";
import "./index.css";

const app = createApp(App);
app.component("FrappeUIProvider", FrappeUIProvider);
app.mount("#root");
