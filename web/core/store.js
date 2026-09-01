// 받아둔 봉을 브라우저에 쌓아 둔다.
//
// 왜 캐시가 필요한가
// -----------------
// **지나간 봉은 변하지 않는다.** 어제 오후 3시 14분의 1분봉은 영원히 그
// 값이다. 그런데 매번 처음부터 받으면 8년치는 200개씩 21,000번을 다시
// 받아야 한다. 한 번 받아 두고 **새로 생긴 것만** 이어 붙이면 몇 초로 끝난다.
//
// 어떻게 담는가
// ------------
// 봉 하나를 레코드 하나로 저장하면 420만 개가 된다. IndexedDB는 레코드마다
// 부가 정보를 붙이므로 그러면 실제 데이터보다 껍데기가 더 커진다. 그래서
// **시간으로 잘라 덩어리로** 담는다. 덩어리 하나가 CHUNK개 분량의 시간을
// 덮고, 그 안의 봉들을 TypedArray 여섯 개로 넣는다.
//
// 경계를 **위치가 아니라 시각으로** 정하는 게 요점이다. 위치로 자르면 앞에
// 과거를 덧붙일 때 모든 경계가 밀려서 전부 다시 써야 한다. 시각으로 자르면
// 어느 봉이 어느 덩어리에 속하는지가 처음부터 정해져 있어서, 앞에 붙이든
// 뒤에 붙이든 **건드린 덩어리만** 다시 쓰면 된다.
//
// 목록을 따로 두는 이유
// --------------------
// "몇 개 갖고 있나"를 알려고 8년치를 다 읽으면 그것만으로 300MB를 읽는다.
// 그래서 덩어리마다 개수와 앞뒤 시각만 적은 **작은 목록**을 나란히 둔다.
// 개수와 기간은 목록만 보고 답하고, 실제 값은 필요한 덩어리만 읽는다.
//
// 파이썬 판(patternscan/data.py)에서 CSV를 제자리에 덮어쓰다가 읽는 쪽이
// 반쯤 쓰인 파일을 보는 사고가 있었다. IndexedDB는 트랜잭션이 통째로
// 성공하거나 통째로 실패하므로 그 문제가 구조적으로 없다.

/** 덩어리 하나가 덮는 봉 수(시간 기준). */
export const CHUNK = 4096;

export const DB_NAME = 'gisigam';
export const DB_VERSION = 1;

/** 그 시각이 몇 번째 덩어리에 속하는지. */
export function chunkOf(ts, step) {
  return Math.floor(ts / (step * CHUNK));
}

/** 봉 배열 -> 저장할 모양. */
export function packChunk(candles) {
  const n = candles.length;
  const out = {
    ts: new Float64Array(n),
    open: new Float64Array(n),
    high: new Float64Array(n),
    low: new Float64Array(n),
    close: new Float64Array(n),
    volume: new Float64Array(n),
  };
  for (let i = 0; i < n; i += 1) {
    const c = candles[i];
    out.ts[i] = c.ts;
    out.open[i] = c.open;
    out.high[i] = c.high;
    out.low[i] = c.low;
    out.close[i] = c.close;
    out.volume[i] = c.volume;
  }
  return out;
}

/** 저장된 모양 -> 봉 배열. */
export function unpackChunk(value) {
  if (!value) return [];
  const out = [];
  for (let i = 0; i < value.ts.length; i += 1) {
    out.push({
      ts: value.ts[i],
      open: value.open[i],
      high: value.high[i],
      low: value.low[i],
      close: value.close[i],
      volume: value.volume[i],
    });
  }
  return out;
}

/**
 * 같은 덩어리에 들어갈 봉들을 합친다. 시각이 겹치면 **새 쪽을 남긴다** —
 * 마지막 봉은 그 분이 끝나기 전에 받으면 아직 확정된 값이 아니라서,
 * 나중에 다시 받은 쪽이 맞다.
 */
export function mergeCandles(existing, incoming) {
  const byTs = new Map();
  for (const c of existing) byTs.set(c.ts, c);
  for (const c of incoming) byTs.set(c.ts, c);
  return [...byTs.values()].sort((a, b) => a.ts - b.ts);
}

/** 봉을 덩어리별로 나눈다. 저장할 때 어느 덩어리를 건드려야 하는지 정한다. */
export function groupByChunk(candles, step) {
  const groups = new Map();
  for (const candle of candles) {
    const key = chunkOf(candle.ts, step);
    const bucket = groups.get(key);
    if (bucket) bucket.push(candle);
    else groups.set(key, [candle]);
  }
  return groups;
}

/**
 * 저장소. 실제 IndexedDB와, 테스트용 메모리 backend 둘 다 이 인터페이스를 쓴다.
 *
 * backend가 해야 할 일:
 *   listIndex(market, timeframe)       -> [{index, n, first, last}] (번호 오름차순)
 *   readChunks(market, timeframe, ids) -> Map(번호 -> 저장값)
 *   writeChunks(market, timeframe, Map(번호 -> {value, meta}))
 *   clear(market, timeframe)
 */
export class CandleStore {
  constructor(backend) {
    this.backend = backend;
  }

  /** 몇 개 갖고 있나. 목록만 보므로 데이터는 읽지 않는다. */
  async count(market, timeframe) {
    const index = await this.backend.listIndex(market, timeframe);
    return index.reduce((total, one) => total + one.n, 0);
  }

  /** 가진 구간의 [처음, 마지막] 유닉스 초. 없으면 null. */
  async span(market, timeframe) {
    const index = (await this.backend.listIndex(market, timeframe)).filter((one) => one.n > 0);
    if (!index.length) return null;
    return [index[0].first, index[index.length - 1].last];
  }

  /**
   * 마지막 `wanted`개만 봉 객체로 읽는다.
   *
   * 계산에 넘길 때는 `loadTailColumns`를 쓴다 — 이쪽은 봉마다 객체를
   * 하나씩 만들어서 8년치에는 못 쓴다. 여기 남겨 두는 이유는 읽기 쉬운
   * 기준 구현이기 때문이고, 시험이 둘의 답이 같은지 확인한다.
   */
  async loadTail(market, timeframe, wanted) {
    if (wanted <= 0) return [];
    const index = await this.backend.listIndex(market, timeframe);
    const picked = [];
    let have = 0;
    for (let i = index.length - 1; i >= 0 && have < wanted; i -= 1) {
      picked.unshift(index[i].index);
      have += index[i].n;
    }
    if (!picked.length) return [];
    const values = await this.backend.readChunks(market, timeframe, picked);
    const out = [];
    for (const id of picked) out.push(...unpackChunk(values.get(id)));
    return out.length > wanted ? out.slice(out.length - wanted) : out;
  }

  /**
   * 마지막 `wanted`개를 **바로 배열 여섯 개로** 읽는다.
   *
   * loadTail은 봉 하나마다 객체를 하나씩 만든다. 30일치 4만 개면 아무
   * 문제가 없지만, 8년치 420만 개면 객체만으로 수백 MB라 아이패드에서
   * 브라우저가 죽는다. 계산에 넘길 때는 어차피 숫자 배열로 펴야 하므로,
   * 중간에 객체를 만들지 않고 곧장 채운다.
   *
   * 돌려주는 것은 `{ts, open, high, low, close, volume, length}`다.
   */
  async loadTailColumns(market, timeframe, wanted) {
    const empty = () => new Float64Array(0);
    if (wanted <= 0) {
      return {
        ts: empty(), open: empty(), high: empty(), low: empty(),
        close: empty(), volume: empty(), length: 0,
      };
    }
    const index = await this.backend.listIndex(market, timeframe);
    const picked = [];
    let have = 0;
    for (let i = index.length - 1; i >= 0 && have < wanted; i -= 1) {
      picked.unshift(index[i]);
      have += index[i].n;
    }
    const total = Math.min(have, wanted);
    const out = {
      ts: new Float64Array(total),
      open: new Float64Array(total),
      high: new Float64Array(total),
      low: new Float64Array(total),
      close: new Float64Array(total),
      volume: new Float64Array(total),
      length: total,
    };
    if (!picked.length) return out;

    // 앞쪽 덩어리는 필요한 만큼만 잘라 쓴다. `have`가 `wanted`보다 클 때
    // 넘치는 부분은 가장 오래된 쪽이므로 앞에서 잘라낸다.
    let skip = have - total;
    let at = 0;
    for (const meta of picked) {
      // eslint-disable-next-line no-await-in-loop
      const values = await this.backend.readChunks(market, timeframe, [meta.index]);
      const chunk = values.get(meta.index);
      if (!chunk) continue;
      const from = Math.min(skip, chunk.ts.length);
      skip -= from;
      const take = chunk.ts.length - from;
      if (take <= 0) continue;
      for (const column of ['ts', 'open', 'high', 'low', 'close', 'volume']) {
        out[column].set(chunk[column].subarray(from, from + take), at);
      }
      at += take;
    }
    return out;
  }

  /** 전부 읽는다. 작은 구간에만 쓸 것. */
  async loadAll(market, timeframe) {
    const index = await this.backend.listIndex(market, timeframe);
    if (!index.length) return [];
    const ids = index.map((one) => one.index);
    const values = await this.backend.readChunks(market, timeframe, ids);
    const out = [];
    for (const id of ids) out.push(...unpackChunk(values.get(id)));
    return out;
  }

  /**
   * 봉을 넣는다. 이미 있는 것과 겹치면 합친다.
   *
   * 건드리는 덩어리만 읽고 쓴다 — 새 봉 세 개를 넣으려고 8년치를 다시
   * 쓰는 일은 없다.
   */
  async put(market, timeframe, step, candles) {
    if (!candles.length) return 0;
    const groups = groupByChunk(candles, step);
    const ids = [...groups.keys()];
    const existing = await this.backend.readChunks(market, timeframe, ids);

    const writes = new Map();
    let added = 0;
    for (const [id, incoming] of groups) {
      const before = unpackChunk(existing.get(id));
      const merged = mergeCandles(before, incoming);
      added += merged.length - before.length;
      writes.set(id, {
        value: packChunk(merged),
        meta: {
          index: id,
          n: merged.length,
          first: merged[0].ts,
          last: merged[merged.length - 1].ts,
        },
      });
    }
    await this.backend.writeChunks(market, timeframe, writes);
    return added;
  }

  async clear(market, timeframe) {
    await this.backend.clear(market, timeframe);
  }
}

// ------------------------------------------------------------ IndexedDB
function request(target) {
  return new Promise((resolve, reject) => {
    target.onsuccess = () => resolve(target.result);
    target.onerror = () => reject(target.error);
  });
}

function finished(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('저장이 취소되었습니다'));
  });
}

/** 브라우저 저장소. */
export class IndexedDbBackend {
  constructor(db) {
    this.db = db;
  }

  static async open(name = DB_NAME) {
    const opening = indexedDB.open(name, DB_VERSION);
    opening.onupgradeneeded = () => {
      const db = opening.result;
      const key = { keyPath: ['market', 'timeframe', 'index'] };
      if (!db.objectStoreNames.contains('chunks')) db.createObjectStore('chunks', key);
      if (!db.objectStoreNames.contains('index')) db.createObjectStore('index', key);
    };
    return new IndexedDbBackend(await request(opening));
  }

  static range(market, timeframe) {
    return IDBKeyRange.bound([market, timeframe, -Infinity], [market, timeframe, Infinity]);
  }

  async listIndex(market, timeframe) {
    const tx = this.db.transaction('index', 'readonly');
    const rows = await request(
      tx.objectStore('index').getAll(IndexedDbBackend.range(market, timeframe)),
    );
    return rows.sort((a, b) => a.index - b.index);
  }

  async readChunks(market, timeframe, ids) {
    const tx = this.db.transaction('chunks', 'readonly');
    const store = tx.objectStore('chunks');
    const out = new Map();
    await Promise.all(ids.map(async (id) => {
      const value = await request(store.get([market, timeframe, id]));
      if (value) out.set(id, value);
    }));
    return out;
  }

  async writeChunks(market, timeframe, entries) {
    // 데이터와 목록을 **한 트랜잭션에서** 쓴다. 따로 쓰면 중간에 끊겼을 때
    // 목록이 실제와 어긋나고, 그러면 있지도 않은 봉을 세게 된다.
    const tx = this.db.transaction(['chunks', 'index'], 'readwrite');
    const chunks = tx.objectStore('chunks');
    const index = tx.objectStore('index');
    for (const [id, { value, meta }] of entries) {
      chunks.put({ market, timeframe, index: id, ...value });
      index.put({ market, timeframe, ...meta });
    }
    await finished(tx);
  }

  async clear(market, timeframe) {
    const tx = this.db.transaction(['chunks', 'index'], 'readwrite');
    const range = IndexedDbBackend.range(market, timeframe);
    tx.objectStore('chunks').delete(range);
    tx.objectStore('index').delete(range);
    await finished(tx);
  }

  /** 종목·간격별로 몇 개씩 갖고 있는지. '저장공간 비우기' 화면이 쓴다. */
  async everything() {
    const tx = this.db.transaction('index', 'readonly');
    const rows = await request(tx.objectStore('index').getAll());
    const out = new Map();
    for (const row of rows) {
      const key = `${row.market}|${row.timeframe}`;
      out.set(key, (out.get(key) ?? 0) + row.n);
    }
    return out;
  }
}

/** 테스트용. IndexedDB가 없는 곳(node)에서 같은 인터페이스를 흉내 낸다. */
export class MemoryBackend {
  constructor() {
    this.chunks = new Map();
    this.index = new Map();
  }

  static key(market, timeframe) {
    return `${market}|${timeframe}`;
  }

  async listIndex(market, timeframe) {
    const bucket = this.index.get(MemoryBackend.key(market, timeframe));
    return bucket ? [...bucket.values()].sort((a, b) => a.index - b.index) : [];
  }

  async readChunks(market, timeframe, ids) {
    const bucket = this.chunks.get(MemoryBackend.key(market, timeframe));
    const out = new Map();
    if (!bucket) return out;
    for (const id of ids) if (bucket.has(id)) out.set(id, bucket.get(id));
    return out;
  }

  async writeChunks(market, timeframe, entries) {
    const key = MemoryBackend.key(market, timeframe);
    const chunks = this.chunks.get(key) ?? new Map();
    const index = this.index.get(key) ?? new Map();
    for (const [id, { value, meta }] of entries) {
      chunks.set(id, value);
      index.set(id, meta);
    }
    this.chunks.set(key, chunks);
    this.index.set(key, index);
  }

  async clear(market, timeframe) {
    this.chunks.delete(MemoryBackend.key(market, timeframe));
    this.index.delete(MemoryBackend.key(market, timeframe));
  }
}
