import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelRequest } from "./requests";

afterEach(() => vi.unstubAllGlobals());

describe("cancelRequest", () => {
  it("202 空响应体不解析 JSON(后端就是空 body)", async () => {
    vi.stubGlobal("fetch", async () => new Response(null, { status: 202 }));
    await expect(cancelRequest("r-000001")).resolves.toBeUndefined();
  });

  it("409 抛后端 detail 的可读错误", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify({ detail: "请求已结束" }), {
          status: 409,
          headers: { "content-type": "application/json" },
        }),
    );
    await expect(cancelRequest("r-000001")).rejects.toThrow("请求已结束");
  });
});
