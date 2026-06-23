import * as React from "react";

export function Toast({ message }) {
	if (!message) return null;
	return <div className="kc-toast">{message}</div>;
}