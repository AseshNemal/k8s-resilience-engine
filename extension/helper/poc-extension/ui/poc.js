(function () {
  "use strict";
  const log = document.getElementById("log");
  const buttons = [document.getElementById("discover"), document.getElementById("dialog")];
  const show = (text) => { log.textContent += "\n" + text; };
  const b64 = (text) => btoa(unescape(encodeURIComponent(text)));
  const dd = window.ddClient;
  const exec = dd && dd.extension && dd.extension.host && dd.extension.host.cli && dd.extension.host.cli.exec;

  async function call(args) {
    const result = await exec("resilience-helper", args);
    return typeof result.stdout === "string" ? result.stdout : JSON.stringify(result);
  }

  // Breadcrumbs are non-secret and let the test see what this page did even if a call fails.
  async function note(text) { try { await call(["note", text]); } catch (_ignored) { /* evidence only */ } }

  async function run(args) {
    show("\n> resilience-helper " + args.join(" "));
    await note("before " + args[0]);
    let received;
    try {
      received = await call(args);
      show(received);
    } catch (error) {
      received = "ERROR: " + (error && (error.message || JSON.stringify(error)));
      show(received);
      await note("exec failed: " + received);
    }
    try { await call(["record", b64(received)]); } catch (_ignored) { /* evidence only */ }
    await note("after " + args[0]);
  }

  async function busy(work) {
    buttons.forEach((b) => (b.disabled = true));
    try { await work(); } finally { buttons.forEach((b) => (b.disabled = false)); }
  }

  log.textContent = "ddClient: " + (dd ? "present" : "MISSING") + "\nhost.cli.exec: " + (exec ? "present" : "MISSING") +
    "\nplatform: " + (dd && dd.host ? dd.host.platform : "unknown");
  if (!exec) { buttons.forEach((b) => (b.disabled = true)); return; }

  document.getElementById("discover").addEventListener("click", async () => { await note("click discover"); busy(() => run(["discover"])); });
  document.getElementById("dialog").addEventListener("click", async () => { await note("click dialog"); busy(() => run(["approve-demo"])); });

  // Runs by itself once per page load so the test does not depend on a click landing:
  // discover, then two demo dialogs (answer Approve to the first and Cancel to the second).
  busy(async () => {
    await note("page loaded, auto sequence starting");
    await run(["discover"]);
    show("\nDIALOG 1 of 2: please click APPROVE (demo, creates nothing)");
    await run(["approve-demo"]);
    show("\nDIALOG 2 of 2: please click CANCEL (demo, creates nothing)");
    await run(["approve-demo"]);
    show("\nAll steps finished.");
    await note("auto sequence finished");
  });
})();
