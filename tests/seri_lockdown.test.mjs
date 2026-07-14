import fs from "node:fs";
import test, { after, before, beforeEach } from "node:test";
import {
  assertFails,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";

const PROJECT_ID = "seri-lockdown-test";
const CODE = "AB12";

let env;

function room(path = "") {
  const database = env.unauthenticatedContext().database();
  return database.ref(`rooms/${CODE}${path ? `/${path}` : ""}`);
}

before(async () => {
  env = await initializeTestEnvironment({
    projectId: PROJECT_ID,
    database: { rules: fs.readFileSync("firebase/firebase_rules.json", "utf8") },
  });
});

beforeEach(async () => {
  await env.clearDatabase();
  await env.withSecurityRulesDisabled(async context => {
    await context.database().ref(`rooms/${CODE}`).set({
      meta: { host: "legacy-host", phase: "playing", n: 3 },
      seats: { 0: { uid: "legacy-host", name: "Host" } },
      priv: { 0: { lens: 1, see: { 0: 4 } } },
      money: { 0: 30 },
      game: { round: 0, phase: "auction", price: 0 },
      hist: { 0: { price: 1 } },
    });
  });
});

after(async () => {
  if (env) await env.cleanup();
});

test("未認証の旧room読取りを拒否する", async () => {
  await assertFails(room().get());
});

test("未認証の旧room書込みを拒否する", async () => {
  await assertFails(room().set({ meta: { host: "attacker", phase: "lobby", n: 2 } }));
});

test("既存roomのpriv読取りを拒否する", async () => {
  await assertFails(room("priv/0").get());
});

test("money改ざんを拒否する", async () => {
  await assertFails(room("money/0").set(999999));
});

test("game改ざんを拒否する", async () => {
  await assertFails(room("game/phase").set("end"));
});

test("seats改ざんを拒否する", async () => {
  await assertFails(room("seats/0/uid").set("attacker"));
});

test("hist書込みを拒否する", async () => {
  await assertFails(room("hist/1").set({ price: "<img src=x onerror=alert(1)>" }));
});

test("旧room全体の削除を拒否する", async () => {
  await assertFails(room().remove());
});
