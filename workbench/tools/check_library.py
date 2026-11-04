"""验证款式库接进工作台：/api/skill 有 13 个款式、参考图可取、预览提示词用到新版型。"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8793"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=120, follow_redirects=False) as client:
        print("login", client.post("/login", data={"username": "admin", "password": "Asd123456."}).status_code)
        skill = client.get("/api/skill").json()
        types = skill["types"]
        print("款式数:", len(types))
        for slug, garment in types.items():
            first = garment["colors"][0]["url"] if garment["colors"] else "-"
            print(f"  {slug:<26} {garment['display_name']:<14} 颜色 {len(garment['colors']):>2}  {first}")

        library_types = [s for s in types if s[0].isdigit()]
        print("\n来自款式表:", len(library_types), "个")

        ok_refs = 0
        for slug in library_types:
            for color in types[slug]["colors"]:
                response = client.get(color["url"])
                if response.status_code == 200 and len(response.content) > 1000:
                    ok_refs += 1
                else:
                    print("  [x] 参考图取不到:", color["label"], response.status_code)
        print("参考图可取:", ok_refs, "张")

        preview = client.post(
            "/api/preview", json={"garment_type": "23 索罗娜短袖", "color": "black"}
        ).json()
        print("\n预览 phrase 命中 Sorona:", "Sorona" in preview["single_pass"],
              "| 颜色:", preview["color"]["hex"])
        preview2 = client.post(
            "/api/preview", json={"garment_type": "06 连帽卫衣", "color": "heather-grey"}
        ).json()
        print("06 卫衣 phrase 命中 hoodie:", "hoodie" in preview2["single_pass"],
              "| 颜色:", preview2["color"]["hex"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
