// konsol#305 U2: saveFile.js
//
// The one place a downloaded file (api.js download's {blob, filename}) is
// handed to the browser to save: Numbers' Export to Excel (8.5) and the
// Audit trail's Export CSV (10.2). The name is the server's; a blank one is
// refused rather than made up. `doc`, `urls` and `defer` are injectable so
// tests never touch a real DOM. Nothing is kept in the browser.
//
// review-w5 U7: the object URL is revoked in a later task (`defer`,
// `setTimeout(…, 0)` by default), never synchronously after `click()`, which
// some browsers answer by aborting the save.

function nextTask(fn) {
	setTimeout(fn, 0);
}

export function saveFile(blob, filename, { doc = document, urls = URL, defer = nextTask } = {}) {
	if (typeof filename !== "string" || !filename) {
		throw new Error("No file name to save the download as.");
	}
	const href = urls.createObjectURL(blob);
	const link = doc.createElement("a");
	link.href = href;
	link.download = filename;
	doc.body.appendChild(link);
	link.click();
	link.remove();
	defer(() => urls.revokeObjectURL(href));
}
