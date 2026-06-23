import * as React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./control.css";

class KonsolControl {
	constructor({ page, wrapper }) {
		this.$wrapper = $(wrapper);
		this.page = page;
		this.root = null;
		this.init();
	}

	init() {
		this.$wrapper.addClass("konsol-control-doppio-root");
		this.root = createRoot(this.$wrapper.get(0));
		this.root.render(<App />);
	}

	destroy() {
		if (this.root) {
			this.root.unmount();
			this.root = null;
		}
	}
}

frappe.provide("frappe.ui");
frappe.ui.KonsolControl = KonsolControl;
export default KonsolControl;