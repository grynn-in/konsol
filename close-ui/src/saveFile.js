// konsol#305 U2: saveFile.js
//
// The one place a downloaded file (api.js download's {blob, filename}) is
// handed to the browser to save: Numbers' Export to Excel (8.5) and the
// Audit trail's Export CSV (10.2). The name is the server's; a blank one is
// refused rather than made up. `doc` and `urls` are injectable so tests
// never touch a real DOM. Nothing is kept in the browser.

export function saveFile(blob, filename, { doc = document, urls = URL } = {}) {
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
	urls.revokeObjectURL(href);
}
