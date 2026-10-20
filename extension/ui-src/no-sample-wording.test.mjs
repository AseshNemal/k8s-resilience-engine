// The app shows live evidence or an explicit error, never sample data. No extension page script may say
// otherwise, because the wording is what a person reads when deciding whether to trust a page.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";

const dir = new URL("./", import.meta.url);
const scripts = readdirSync(dir).filter((name) => name.endsWith(".js"));

test("there are extension page scripts to check", () => {
    assert.ok(scripts.length > 0);
});

for (const name of scripts) {
    test(`${name} never says the results are sample data`, () => {
        const source = readFileSync(new URL(name, dir), "utf8");
        assert.doesNotMatch(source, /sample data|built-in sample|demo data/i);
    });
}
