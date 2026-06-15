frappe.ui.form.on("Pipeline Run", {
    refresh(frm) {
        // Status indicator
        const status_color = {
            "Queued": "orange",
            "Extracting": "blue",
            "Transforming": "blue",
            "Completed": "green",
            "Failed": "red",
        };
        if (frm.doc.status) {
            frm.page.set_indicator(frm.doc.status, status_color[frm.doc.status] || "grey");
        }

        // "Run Pipeline" button on new docs
        if (frm.is_new()) {
            frm.add_custom_button(__("Run Pipeline"), function () {
                frappe.call({
                    method: "konsol.pipeline.doctype.pipeline_run.pipeline_run.trigger_pipeline",
                    callback(r) {
                        if (r.message) {
                            frappe.set_route("Form", "Pipeline Run", r.message);
                            frappe.show_alert({
                                message: __("Pipeline triggered: {0}", [r.message]),
                                indicator: "green",
                            });
                        }
                    },
                });
            }).addClass("btn-primary");
        }

        // Realtime listener for progress updates
        frappe.realtime.on("pipeline_progress", function (data) {
            if (data.name === frm.doc.name) {
                frm.reload_doc();
            }
        });

        // Live build log + per-step streaming (Press-style)
        frm._pipe_log = frm.doc.log || "";
        frappe.realtime.on("pipeline_run_update", function (data) {
            if (!data || data.run !== frm.doc.name) return;
            if (data.line !== undefined) {
                frm._pipe_log = (frm._pipe_log || "") + data.line + "\n";
                frm.set_value("log", frm._pipe_log.slice(-20000));
            }
            if (data.progress !== undefined) frm.set_value("progress_pct", data.progress);
            if (data.done) frm.reload_doc();
        });
    },
});

// List view customization
frappe.listview_settings["Pipeline Run"] = {
    add_fields: ["status"],
    get_indicator(doc) {
        const colors = {
            "Queued": "orange",
            "Extracting": "blue",
            "Transforming": "blue",
            "Completed": "green",
            "Failed": "red",
        };
        return [__(doc.status), colors[doc.status] || "grey", "status,=," + doc.status];
    },
    onload(listview) {
        listview.page.add_inner_button(__("New Pipeline Run"), function () {
            frappe.call({
                method: "konsol.pipeline.doctype.pipeline_run.pipeline_run.trigger_pipeline",
                callback(r) {
                    if (r.message) {
                        frappe.set_route("Form", "Pipeline Run", r.message);
                    }
                },
            });
        });
    },
};
