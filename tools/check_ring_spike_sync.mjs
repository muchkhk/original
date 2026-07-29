// 指示書01・技術スパイクの検証スクリプト（§9-c: 成功/失敗パスが別の出力になることを確認する）。
// Firebase RTDB Emulator上に2つの独立クライアント（別UID・別SDKインスタンス）を立て、
// 追記型の ringSpikeRooms/{code}/events を介して越境イベント(crossing)を同期できるか、
// ping/pongでの往復遅延(RTT)を実測できるかを検証する。
//
// 使い方（先に `npm run emulators:ring-spike` でEmulatorを起動しておくこと）:
//   node tools/check_ring_spike_sync.mjs            # 正常系。RESULT: PASS を期待
//   node tools/check_ring_spike_sync.mjs --break     # 異常系。RESULT: FAIL を期待
//     （--break は意図的にB側のルームコードを変え、越境イベントが届かない状態を再現する）
import { initializeApp } from "firebase/app";
import { getAuth, connectAuthEmulator, signInAnonymously } from "firebase/auth";
import { getDatabase, connectDatabaseEmulator, ref, push, set, onChildAdded, get } from "firebase/database";

const BREAK = process.argv.includes("--break");
// 実行ごとに一意なコードにする（Emulatorを再起動せず連続実行しても、
// 前回実行で作成済みのmetaに !data.exists() ルールで弾かれないようにするため）。
const ROOM_CODE = "SPIKECHK_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
const CONFIG = {
  apiKey: "fake-api-key-emulator-only",
  authDomain: "ring-spike-test.firebaseapp.com",
  databaseURL: "https://ring-spike-test-default-rtdb.firebaseio.com",
  projectId: "ring-spike-test",
};

function makeClient(name) {
  const app = initializeApp(CONFIG, name);
  const auth = getAuth(app);
  connectAuthEmulator(auth, "http://127.0.0.1:9399", { disableWarnings: true });
  const db = getDatabase(app);
  connectDatabaseEmulator(db, "127.0.0.1", 9370);
  return { app, auth, db };
}

function timeout(ms, label) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error(`timeout: ${label}`)), ms));
}

async function main() {
  const a = makeClient("clientA");
  const b = makeClient("clientB");

  const uidA = (await signInAnonymously(a.auth)).user.uid;
  const uidB = (await signInAnonymously(b.auth)).user.uid;
  console.log(`signIn OK: A=...${uidA.slice(-4)} B=...${uidB.slice(-4)}`);

  const roomA = ref(a.db, `ringSpikeRooms/${ROOM_CODE}`);
  const roomBCode = BREAK ? ROOM_CODE + "_WRONG" : ROOM_CODE;
  const roomB = ref(b.db, `ringSpikeRooms/${roomBCode}`);

  await set(ref(a.db, `ringSpikeRooms/${ROOM_CODE}/meta`), {
    hostUid: uidA, createdAt: Date.now(), N: 4,
  });
  console.log(`room ${ROOM_CODE} created by A`);

  if (BREAK) {
    // 異常系：Bが存在しない部屋コードを購読する（本来はUIのバグで起きうる状態）。
    // meta.exists()がfalseのため、Rules上そもそもeventsの.readが拒否される想定。
    try {
      await get(ref(b.db, `ringSpikeRooms/${roomBCode}/meta`));
    } catch (e) {
      // 想定内。下のonChildAdded購読自体が権限エラーで失敗するはず。
    }
  }

  // --- 越境イベント(crossing)の同期確認 ---
  const received = [];
  const unsubCrossing = onChildAdded(ref(b.db, `ringSpikeRooms/${roomBCode}/events`), (snap) => {
    const ev = snap.val();
    if (ev && ev.kind === "crossing") received.push(ev);
  }, (err) => {
    console.error(`FAIL: B's events subscription error: ${err.message}`);
  });

  const crossingEvent = {
    ts: Date.now(), kind: "crossing", clientId: "clientA",
    fromWorld: 0, toWorld: 1, level: 1, x: 42, y: 0, vx: 0, vy: -3,
  };
  await push(ref(a.db, `ringSpikeRooms/${ROOM_CODE}/events`), crossingEvent);
  console.log("A pushed crossing event 0->1");

  try {
    await Promise.race([
      new Promise((resolve) => {
        const iv = setInterval(() => {
          if (received.length > 0) { clearInterval(iv); resolve(); }
        }, 50);
      }),
      timeout(5000, "crossing sync"),
    ]);
    console.log(`PASS: B received crossing event: ${JSON.stringify(received[0])}`);
  } catch (e) {
    console.error(`FAIL: crossing event did not reach B within 5s (${e.message})`);
    process.exitCode = 1;
  }

  if (process.exitCode === 1) {
    console.log("RESULT: FAIL");
    return;
  }

  // --- ping/pong 往復遅延(RTT)の実測 ---
  const pendingPings = {};
  const rtts = [];

  onChildAdded(ref(b.db, `ringSpikeRooms/${roomBCode}/events`), async (snap) => {
    const ev = snap.val();
    if (ev && ev.kind === "ping" && ev.clientId === "clientA") {
      await push(ref(b.db, `ringSpikeRooms/${roomBCode}/events`), {
        ts: Date.now(), kind: "pong", clientId: "clientB", refId: snap.key,
      });
    }
  });
  onChildAdded(ref(a.db, `ringSpikeRooms/${ROOM_CODE}/events`), (snap) => {
    const ev = snap.val();
    if (ev && ev.kind === "pong" && pendingPings[ev.refId]) {
      const rtt = Date.now() - pendingPings[ev.refId];
      delete pendingPings[ev.refId];
      rtts.push(rtt);
    }
  });

  const N_PINGS = 5;
  for (let i = 0; i < N_PINGS; i++) {
    const pingRef = push(ref(a.db, `ringSpikeRooms/${ROOM_CODE}/events`));
    pendingPings[pingRef.key] = Date.now();
    await set(pingRef, { ts: Date.now(), kind: "ping", clientId: "clientA" });
    await new Promise((r) => setTimeout(r, 150));
  }

  try {
    await Promise.race([
      new Promise((resolve) => {
        const iv = setInterval(() => {
          if (rtts.length >= N_PINGS) { clearInterval(iv); resolve(); }
        }, 50);
      }),
      timeout(6000, "ping/pong RTT"),
    ]);
  } catch (e) {
    console.error(`FAIL: only ${rtts.length}/${N_PINGS} pong replies within timeout (${e.message})`);
    process.exitCode = 1;
  }

  if (rtts.length > 0) {
    const mean = rtts.reduce((s, v) => s + v, 0) / rtts.length;
    console.log(`RTT samples(ms): ${rtts.join(", ")}`);
    console.log(`RTT mean=${mean.toFixed(1)}ms min=${Math.min(...rtts)}ms max=${Math.max(...rtts)}ms (n=${rtts.length})`);
  }

  if (process.exitCode === 1) {
    console.log("RESULT: FAIL");
  } else {
    console.log("RESULT: PASS");
  }
}

main().catch((e) => {
  console.error(`FAIL: ${e.stack || e.message}`);
  process.exitCode = 1;
  console.log("RESULT: FAIL");
});
