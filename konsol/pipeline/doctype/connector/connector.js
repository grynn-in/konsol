frappe.ui.form.on("Connector", {
  refresh(frm) {
    if (frm.is_new() || !frm.doc.erp_type) {
      return;
    }

    const supported = ["d365_fo", "d365_bc", "erpnext"].includes(frm.doc.erp_type);
    if (!supported) {
      return;
    }

    if (frm.doc.writeback_enabled) {
      frm.add_custom_button(__("Test Writeback"), () => {
        frm.call("test_writeback_connection").then((r) => {
          const result = r.message || {};
          if (result.ok) {
            frappe.show_alert({
              message: result.message || __("Write-back credentials validated."),
              indicator: "green",
            });
          } else {
            frappe.msgprint({
              title: __("Writeback Check Failed"),
              message:
                result.message ||
                __("Write-back credentials could not be validated."),
              indicator: "red",
            });
          }
        });
      }, __("Airbyte"));
    }

    frm.add_custom_button(__("Test Extract"), () => {
      frm.call("test_extract_connection").then((r) => {
        const result = r.message || {};
        if (result.ok) {
          frappe.show_alert({
            message: result.message || __("Extract credentials validated."),
            indicator: "green",
          });
        } else {
          frappe.msgprint({
            title: __("Extract Check Failed"),
            message: result.message || __("Extract credentials could not be validated."),
            indicator: "red",
          });
        }
      });
    }, __("Airbyte"));

    frm.add_custom_button(__("Test & Provision Airbyte"), () => {
      frm.call("provision_airbyte").then((r) => {
        const result = r.message || {};
        if (result.ok) {
          frm.reload_doc();
          frappe.show_alert({
            message: __("Airbyte provisioned for {0}", [frm.doc.connector_name]),
            indicator: "green",
          });
        }
      });
    }, __("Airbyte"));
  },
});