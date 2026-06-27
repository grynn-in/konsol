// konsol-exec orchestrator console (PRD-11)
//
// A Desk single-page app over the PRD-10 whitelisted orchestrator API:
//   - a launch form (fiscal_year / fiscal_period / scope / full_refresh /
//     skip_sync + optional definition) that POSTs to
//     `konsol.orchestrator.api.start_run`,
//   - a live step timeline rendered from the Pipeline Run's `steps` child rows
//     (PRD-6 fields: step_id, step_type, status, started_at, ended_at, rows,
//     output, error), updated in place off the `orchestrator_step` realtime
//     event the FrappeSink emits, and
//   - retry / resume / cancel actions wired to
//     `konsol.orchestrator.api.retry_step` / `.resume_run` / `.cancel_run`.

frappe.pages["konsol-exec"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Konsol Exec"),
		single_column: true,
	});

	const console_ = new KonsolExecConsole(page);
	wrapper.konsol_exec = console_;
};

class KonsolExecConsole {
	constructor(page) {
		this.page = page;
		this.current_run = null;
		this.selected_step = null;
		this.$body = $(
			'<div class="konsol-exec-console">' +
				'<div class="ke-launch"></div>' +
				'<div class="ke-run-meta text-muted"></div>' +
				'<div class="ke-timeline"></div>' +
				"</div>"
		).appendTo(page.main);
		this.build_launch_form();
		this.bind_realtime();
	}

	// --- launch form -----------------------------------------------------
	build_launch_form() {
		const $launch = this.$body.find(".ke-launch");
		this.fields = {};
		this.form = new frappe.ui.FieldGroup({
			body: $launch.get(0),
			fields: [
				{
					fieldname: "fiscal_year",
					label: __("Fiscal Year"),
					fieldtype: "Int",
				},
				{
					fieldname: "fiscal_period",
					label: __("Fiscal Period"),
					fieldtype: "Int",
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "scope",
					label: __("Scope (dbt select)"),
					fieldtype: "Data",
				},
				{
					fieldname: "definition",
					label: __("Pipeline Definition"),
					fieldtype: "Data",
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "full_refresh",
					label: __("Full Refresh"),
					fieldtype: "Check",
				},
				{
					fieldname: "skip_sync",
					label: __("Skip Airbyte Sync"),
					fieldtype: "Check",
				},
			],
		});
		this.form.make();

		this.page.set_primary_action(__("Start Run"), () => this.start_run());
	}

	collect_params() {
		const v = this.form.get_values() || {};
		const params = {};
		if (v.fiscal_year) params.fiscal_year = v.fiscal_year;
		if (v.fiscal_period) params.fiscal_period = v.fiscal_period;
		if (v.scope) params.scope = v.scope;
		params.full_refresh = v.full_refresh ? 1 : 0;
		params.skip_sync = v.skip_sync ? 1 : 0;
		return { definition: v.definition || null, params };
	}

	// --- start -----------------------------------------------------------
	start_run() {
		const { definition, params } = this.collect_params();
		frappe.call({
			method: "konsol.orchestrator.api.start_run",
			args: { definition: definition, params: params },
			freeze: true,
			freeze_message: __("Starting pipeline run..."),
			callback: (r) => {
				if (r && r.message) {
					this.load_run(r.message);
					frappe.show_alert({
						message: __("Run {0} queued", [r.message]),
						indicator: "blue",
					});
				}
			},
		});
	}

	// --- load + render run ----------------------------------------------
	load_run(run_name) {
		this.current_run = run_name;
		frappe.db.get_doc("Pipeline Run", run_name).then((doc) => {
			this.render_run(doc);
		});
	}

	render_run(doc) {
		const $meta = this.$body.find(".ke-run-meta");
		$meta.html(
			`<b>${frappe.utils.escape_html(doc.name)}</b> — ${this.indicator_html(
				doc.status
			)}`
		);
		this.render_timeline(doc.steps || []);
	}

	// --- timeline --------------------------------------------------------
	render_timeline(steps) {
		const $timeline = this.$body.find(".ke-timeline");
		$timeline.empty();
		if (!steps.length) {
			$timeline.html(`<div class="text-muted">${__("No steps yet.")}</div>`);
			return;
		}
		steps.forEach((step) => {
			$timeline.append(this.render_step(step));
		});
	}

	render_step(step) {
		const $row = $(
			'<div class="ke-step" data-step-id="' +
				frappe.utils.escape_html(step.step_id || "") +
				'"></div>'
		);
		const started = step.started_at || "";
		const ended = step.ended_at || "";
		$row.html(
			`<div class="ke-step-head">` +
				`<span class="ke-step-id">${frappe.utils.escape_html(
					step.step_id || ""
				)}</span> ` +
				`<span class="ke-step-type text-muted">${frappe.utils.escape_html(
					step.step_type || ""
				)}</span> ` +
				this.indicator_html(step.status) +
				`</div>` +
				`<div class="ke-step-times text-muted small">` +
				`${frappe.utils.escape_html(started)} → ${frappe.utils.escape_html(
					ended
				)} · ${step.rows || 0} ${__("rows")}</div>` +
				(step.output
					? `<pre class="ke-step-output">${frappe.utils.escape_html(
							step.output
					  )}</pre>`
					: "") +
				(step.error
					? `<pre class="ke-step-error text-danger">${frappe.utils.escape_html(
							step.error
					  )}</pre>`
					: "")
		);
		this.add_step_actions($row, step);
		return $row;
	}

	add_step_actions($row, step) {
		const $actions = $('<div class="ke-step-actions"></div>').appendTo($row);
		const run_name = this.current_run;
		const step_id = step.step_id;

		$(`<button class="btn btn-xs btn-default">${__("Retry")}</button>`)
			.appendTo($actions)
			.on("click", () => this.retry_step(run_name, step_id));

		$(`<button class="btn btn-xs btn-default">${__("Resume")}</button>`)
			.appendTo($actions)
			.on("click", () => this.resume_run(run_name, step_id));

		$(`<button class="btn btn-xs btn-danger">${__("Cancel")}</button>`)
			.appendTo($actions)
			.on("click", () => this.cancel_run(run_name));
	}

	// --- actions (PRD-10) ------------------------------------------------
	retry_step(run_name, step_id) {
		frappe.call({
			method: "konsol.orchestrator.api.retry_step",
			args: { run_name: run_name, step_id: step_id },
			callback: () => this.load_run(run_name),
		});
	}

	resume_run(run_name, step_id) {
		frappe.call({
			method: "konsol.orchestrator.api.resume_run",
			args: { run_name: run_name, step_id: step_id },
			callback: () => this.load_run(run_name),
		});
	}

	cancel_run(run_name) {
		// NOTE: cancel is best-effort — it stops a not-yet-started run and an
		// in-process executor; a run already mid-step in a worker finishes that
		// step first (cooperative cross-worker cancel is a P2 follow-up).
		frappe.call({
			method: "konsol.orchestrator.api.cancel_run",
			args: { run_name: run_name },
			callback: () => {
				frappe.show_alert({
					message: __("Cancellation takes effect at the next step."),
					indicator: "orange",
				});
				this.load_run(run_name);
			},
		});
	}

	// --- live updates ----------------------------------------------------
	bind_realtime() {
		frappe.realtime.on("orchestrator_step", (data) => {
			if (!data || !this.current_run) return;
			// reload the run doc so the timeline reflects the persisted child rows
			this.load_run(this.current_run);
		});
	}

	// --- helpers ---------------------------------------------------------
	indicator_html(status) {
		const colors = {
			Success: "green",
			Failed: "red",
			Running: "blue",
			Queued: "orange",
			Pending: "gray",
			Skipped: "gray",
			Cancelled: "red",
		};
		const color = colors[status] || "gray";
		return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(
			status || "Pending"
		)}</span>`;
	}
}
