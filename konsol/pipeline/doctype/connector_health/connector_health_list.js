// Colour each Connector Health row by sync status so an operator can scan the
// list for red (Failed/Stale) at a glance.
frappe.listview_settings["Connector Health"] = {
    add_fields: ["last_sync_status", "lag_minutes"],
    get_indicator: function (doc) {
        const map = {
            Succeeded: ["Succeeded", "green", "last_sync_status,=,Succeeded"],
            Running: ["Running", "blue", "last_sync_status,=,Running"],
            Never: ["Never synced", "gray", "last_sync_status,=,Never"],
            Stale: ["Stale", "orange", "last_sync_status,=,Stale"],
            Failed: ["Failed", "red", "last_sync_status,=,Failed"],
        };
        return map[doc.last_sync_status] || ["Unknown", "gray", ""];
    },
};
