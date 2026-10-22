#!/usr/bin/env bash
# renew-cert.sh 的单测 (10/22): 用假 docker 跑, 不需要真的 Docker / 镜像。
#   bash test_renew_cert.sh      退出码 0 = 全过
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0
ok()  { echo "  ✓ $1"; pass=$((pass+1)); }
bad() { echo "  ✗ $1"; fail=$((fail+1)); }

setup_case() {   # $1=issuer  → 建一个临时装机目录 + 假 docker
    T=$(mktemp -d); mkdir -p "$T/certs" "$T/tools" "$T/bin"
    cp "$HERE/renew-cert.sh" "$T/"
    printf 'X=1\nCATFISH_OIDC_ISSUER=%s\n' "$1" > "$T/.env"
    echo "OLD CERT" > "$T/certs/cert.pem"
    cat > "$T/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
case "$1" in
  inspect) echo "catfish-identity:test" ;;
  run)
    [ "${CERTGEN_FAIL:-0}" = 1 ] && { echo "boom"; exit 1; }
    for a in "$@"; do case "$a" in *:/certs) d="${a%%:/certs}";; esac; done
    if [ "${RENEW:-0}" = 1 ]; then echo "NEW CERT" > "$d/cert.pem"; echo "  ✓ 回读核对通过 · 397 天后到期"
    else echo "  ✓ 现有证书仍有效 · 200 天后到期"; fi ;;
  exec) [ "${RELOAD_FAIL:-0}" = 1 ] && exit 1; exit 0 ;;
esac
DOCKER
    chmod +x "$T/bin/docker"
    export CALLS="$T/calls"; : > "$CALLS"
}
run() { (cd "$T" && PATH="$T/bin:$PATH" bash renew-cert.sh) > "$T/out" 2>&1; echo $?; }

echo "renew-cert.sh"
setup_case "http://10.0.0.8:8998"; rc=$(run)
[ "$rc" = 0 ] && ! grep -q "docker run" "$CALLS" && ok "HTTP 模式跳过, 不碰证书" || bad "HTTP 模式 (rc=$rc)"

setup_case "https://10.0.0.8"; rc=$(run)
[ "$rc" = 0 ] && grep -q -- "--ip 10.0.0.8 --out /certs" "$CALLS" && ! grep -q "docker exec" "$CALLS" \
  && grep -q "200 天后到期" "$T/out" && ok "没到期: 跑检查、不 reload" || bad "没到期 (rc=$rc) $(cat "$T/out")"

setup_case "https://192.168.205.40:8443"; RENEW=1 rc=$(RENEW=1 run)
[ "$rc" = 0 ] && grep -q -- "--ip 192.168.205.40 " "$CALLS" && grep -q "docker exec catfish-web nginx -s reload" "$CALLS" \
  && grep -q "已续期" "$T/out" && ok "重签了: 带端口的地址也取对 IP, 并让 nginx 重新加载" || bad "重签 (rc=$rc) $(cat "$T/out")"

setup_case "https://10.0.0.8"; rc=$(RENEW=1 RELOAD_FAIL=1 run)
[ "$rc" = 1 ] && grep -q "docker restart catfish-web" "$T/out" && ok "重签了但 reload 失败: 报错并说怎么办" || bad "reload 失败 (rc=$rc)"

setup_case "https://10.0.0.8"; rc=$(CERTGEN_FAIL=1 run)
[ "$rc" = 1 ] && grep -q "boom" "$T/out" && ok "certgen 失败: 退出码 1 并带上原因" || bad "certgen 失败 (rc=$rc)"

setup_case "https://10.0.0.8"; rm "$T/certs/cert.pem"; rc=$(run)
[ "$rc" = 0 ] && ! grep -q "docker run" "$CALLS" && ok "还没生成过证书: 跳过 (由 setup.sh 生成)" || bad "无证书 (rc=$rc)"

echo "通过 $pass · 失败 $fail"
[ "$fail" = 0 ]
