#!/bin/bash
# Самостоятельная проверка Сетевого города на macOS. Зависимости: curl, plutil.
# Запуск: bash check-regions.command [часть названия региона]
set -u
export LC_ALL=en_US.UTF-8
MATCH="${1:-}"
TIMEOUT="${NETSCHOOL_PROBE_TIMEOUT:-15}"
case "$TIMEOUT" in ''|*[!0-9]*) echo "NETSCHOOL_PROBE_TIMEOUT должен быть целым числом секунд"; exit 2;; esac
WORK="$(mktemp -d "${TMPDIR:-/tmp}/netschool-regions.XXXXXX")" || exit 1
trap 'rm -rf "$WORK"' EXIT
trap 'exit 130' INT TERM
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPORT="$SCRIPT_DIR/regions-$(date +%Y%m%d-%H%M%S).tsv"
if ! : > "$REPORT" 2>/dev/null; then
  REPORT="$HOME/Desktop/regions-$(date +%Y%m%d-%H%M%S).tsv"
  : > "$REPORT" || exit 1
fi
# Дополнительные CA используются только для этих запросов, в систему не устанавливаются.
if [ -r /etc/ssl/cert.pem ]; then cat /etc/ssl/cert.pem > "$WORK/ca.pem"; else : > "$WORK/ca.pem"; fi
cat >> "$WORK/ca.pem" <<'NETSCHOOL_CA'
# Удостоверяющий центр Минцифры (gu-st.ru): им подписаны сертификаты части
# школьных серверов «Сетевого города», например edu.admoblkaluga.ru.
# Root SHA-256 D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31, до 2032-02-27
# Sub CA SHA-256 BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B, до 2027-03-06
# Sub CA 2024 (nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt) SHA-256 21:55:78:50:36:C9:00:DB:B5:F1:BB:2A:15:69:C8:0C:55:59:5B:D6:BF:94:86:7A:29:BB:DD:BC:7D:88:A3:F2, до 2029-07-19
-----BEGIN CERTIFICATE-----
MIIFwjCCA6qgAwIBAgICEAAwDQYJKoZIhvcNAQELBQAwcDELMAkGA1UEBhMCUlUx
PzA9BgNVBAoMNlRoZSBNaW5pc3RyeSBvZiBEaWdpdGFsIERldmVsb3BtZW50IGFu
ZCBDb21tdW5pY2F0aW9uczEgMB4GA1UEAwwXUnVzc2lhbiBUcnVzdGVkIFJvb3Qg
Q0EwHhcNMjIwMzAxMjEwNDE1WhcNMzIwMjI3MjEwNDE1WjBwMQswCQYDVQQGEwJS
VTE/MD0GA1UECgw2VGhlIE1pbmlzdHJ5IG9mIERpZ2l0YWwgRGV2ZWxvcG1lbnQg
YW5kIENvbW11bmljYXRpb25zMSAwHgYDVQQDDBdSdXNzaWFuIFRydXN0ZWQgUm9v
dCBDQTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAMfFOZ8pUAL3+r2n
qqE0Zp52selXsKGFYoG0GM5bwz1bSFtCt+AZQMhkWQheI3poZAToYJu69pHLKS6Q
XBiwBC1cvzYmUYKMYZC7jE5YhEU2bSL0mX7NaMxMDmH2/NwuOVRj8OImVa5s1F4U
zn4Kv3PFlDBjjSjXKVY9kmjUBsXQrIHeaqmUIsPIlNWUnimXS0I0abExqkbdrXbX
YwCOXhOO2pDUx3ckmJlCMUGacUTnylyQW2VsJIyIGA8V0xzdaeUXg0VZ6ZmNUr5Y
Ber/EAOLPb8NYpsAhJe2mXjMB/J9HNsoFMBFJ0lLOT/+dQvjbdRZoOT8eqJpWnVD
U+QL/qEZnz57N88OWM3rabJkRNdU/Z7x5SFIM9FrqtN8xewsiBWBI0K6XFuOBOTD
4V08o4TzJ8+Ccq5XlCUW2L48pZNCYuBDfBh7FxkB7qDgGDiaftEkZZfApRg2E+M9
G8wkNKTPLDc4wH0FDTijhgxR3Y4PiS1HL2Zhw7bD3CbslmEGgfnnZojNkJtcLeBH
BLa52/dSwNU4WWLubaYSiAmA9IUMX1/RpfpxOxd4Ykmhz97oFbUaDJFipIggx5sX
ePAlkTdWnv+RWBxlJwMQ25oEHmRguNYf4Zr/Rxr9cS93Y+mdXIZaBEE0KS2iLRqa
OiWBki9IMQU4phqPOBAaG7A+eP8PAgMBAAGjZjBkMB0GA1UdDgQWBBTh0YHlzlpf
BKrS6badZrHF+qwshzAfBgNVHSMEGDAWgBTh0YHlzlpfBKrS6badZrHF+qwshzAS
BgNVHRMBAf8ECDAGAQH/AgEEMA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsF
AAOCAgEAALIY1wkilt/urfEVM5vKzr6utOeDWCUczmWX/RX4ljpRdgF+5fAIS4vH
tmXkqpSCOVeWUrJV9QvZn6L227ZwuE15cWi8DCDal3Ue90WgAJJZMfTshN4OI8cq
W9E4EG9wglbEtMnObHlms8F3CHmrw3k6KmUkWGoa+/ENmcVl68u/cMRl1JbW2bM+
/3A+SAg2c6iPDlehczKx2oa95QW0SkPPWGuNA/CE8CpyANIhu9XFrj3RQ3EqeRcS
AQQod1RNuHpfETLU/A2gMmvn/w/sx7TB3W5BPs6rprOA37tutPq9u6FTZOcG1Oqj
C/B7yTqgI7rbyvox7DEXoX7rIiEqyNNUguTk/u3SZ4VXE2kmxdmSh3TQvybfbnXV
4JbCZVaqiZraqc7oZMnRoWrXRG3ztbnbes/9qhRGI7PqXqeKJBztxRTEVj8ONs1d
WN5szTwaPIvhkhO3CO5ErU2rVdUr89wKpNXbBODFKRtgxUT70YpmJ46VVaqdAhOZ
D9EUUn4YaeLaS8AjSF/h7UkjOibNc4qVDiPP+rkehFWM66PVnP1Msh93tc+taIfC
EYVMxjh8zNbFuoc7fzvvrFILLe7ifvEIUqSVIC/AzplM/Jxw7buXFeGP1qVCBEHq
391d/9RAfaZ12zkwFsl+IKwE/OZxW8AHa9i1p4GO0YSNuczzEm4=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIHQjCCBSqgAwIBAgICEAIwDQYJKoZIhvcNAQELBQAwcDELMAkGA1UEBhMCUlUx
PzA9BgNVBAoMNlRoZSBNaW5pc3RyeSBvZiBEaWdpdGFsIERldmVsb3BtZW50IGFu
ZCBDb21tdW5pY2F0aW9uczEgMB4GA1UEAwwXUnVzc2lhbiBUcnVzdGVkIFJvb3Qg
Q0EwHhcNMjIwMzAyMTEyNTE5WhcNMjcwMzA2MTEyNTE5WjBvMQswCQYDVQQGEwJS
VTE/MD0GA1UECgw2VGhlIE1pbmlzdHJ5IG9mIERpZ2l0YWwgRGV2ZWxvcG1lbnQg
YW5kIENvbW11bmljYXRpb25zMR8wHQYDVQQDDBZSdXNzaWFuIFRydXN0ZWQgU3Vi
IENBMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA9YPqBKOk19NFymrE
wehzrhBEgT2atLezpduB24mQ7CiOa/HVpFCDRZzdxqlh8drku408/tTmWzlNH/br
HuQhZ/miWKOf35lpKzjyBd6TPM23uAfJvEOQ2/dnKGGJbsUo1/udKSvxQwVHpVv3
S80OlluKfhWPDEXQpgyFqIzPoxIQTLZ0deirZwMVHarZ5u8HqHetRuAtmO2ZDGQn
vVOJYAjls+Hiueq7Lj7Oce7CQsTwVZeP+XQx28PAaEZ3y6sQEt6rL06ddpSdoTMp
BnCqTbxW+eWMyjkIn6t9GBtUV45yB1EkHNnj2Ex4GwCiN9T84QQjKSr+8f0psGrZ
vPbCbQAwNFJjisLixnjlGPLKa5vOmNwIh/LAyUW5DjpkCx004LPDuqPpFsKXNKpa
L2Dm6uc0x4Jo5m+gUTVORB6hOSzWnWDj2GWfomLzzyjG81DRGFBpco/O93zecsIN
3SL2Ysjpq1zdoS01CMYxie//9zWvYwzI25/OZigtnpCIrcd2j1Y6dMUFQAzAtHE+
qsXflSL8HIS+IJEFIQobLlYhHkoE3avgNx5jlu+OLYe0dF0Ykx1PGNjbwqvTX37R
Cn32NMjlotW2QcGEZhDKj+3urZizp5xdTPZitA+aEjZM/Ni71VOdiOP0igbw6asZ
2fxdozZ1TnSSYNYvNATwthNmZysCAwEAAaOCAeUwggHhMBIGA1UdEwEB/wQIMAYB
Af8CAQAwDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQWBBTR4XENCy2BTm6KSo9MI7NM
XqtpCzAfBgNVHSMEGDAWgBTh0YHlzlpfBKrS6badZrHF+qwshzCBxwYIKwYBBQUH
AQEEgbowgbcwOwYIKwYBBQUHMAKGL2h0dHA6Ly9yb3N0ZWxlY29tLnJ1L2NkcC9y
b290Y2Ffc3NsX3JzYTIwMjIuY3J0MDsGCCsGAQUFBzAChi9odHRwOi8vY29tcGFu
eS5ydC5ydS9jZHAvcm9vdGNhX3NzbF9yc2EyMDIyLmNydDA7BggrBgEFBQcwAoYv
aHR0cDovL3JlZXN0ci1wa2kucnUvY2RwL3Jvb3RjYV9zc2xfcnNhMjAyMi5jcnQw
gbAGA1UdHwSBqDCBpTA1oDOgMYYvaHR0cDovL3Jvc3RlbGVjb20ucnUvY2RwL3Jv
b3RjYV9zc2xfcnNhMjAyMi5jcmwwNaAzoDGGL2h0dHA6Ly9jb21wYW55LnJ0LnJ1
L2NkcC9yb290Y2Ffc3NsX3JzYTIwMjIuY3JsMDWgM6Axhi9odHRwOi8vcmVlc3Ry
LXBraS5ydS9jZHAvcm9vdGNhX3NzbF9yc2EyMDIyLmNybDANBgkqhkiG9w0BAQsF
AAOCAgEARBVzZls79AdiSCpar15dA5Hr/rrT4WbrOfzlpI+xrLeRPrUG6eUWIW4v
Sui1yx3iqGLCjPcKb+HOTwoRMbI6ytP/ndp3TlYua2advYBEhSvjs+4vDZNwXr/D
anbwIWdurZmViQRBDFebpkvnIvru/RpWud/5r624Wp8voZMRtj/cm6aI9LtvBfT9
cfzhOaexI/99c14dyiuk1+6QhdwKaCRTc1mdfNQmnfWNRbfWhWBlK3h4GGE9JK33
Gk8ZS8DMrkdAh0xby4xAQ/mSWAfWrBmfzlOqGyoB1U47WTOeqNbWkkoAP2ys94+s
Jg4NTkiDVtXRF6nr6fYi0bSOvOFg0IQrMXO2Y8gyg9ARdPJwKtvWX8VPADCYMiWH
h4n8bZokIrImVKLDQKHY4jCsND2HHdJfnrdL2YJw1qFskNO4cSNmZydw0Wkgjv9k
F+KxqrDKlB8MZu2Hclph6v/CZ0fQ9YuE8/lsHZ0Qc2HyiSMnvjgK5fDc3TD4fa8F
E8gMNurM+kV8PT8LNIM+4Zs+LKEV8nqRWBaxkIVJGekkVKO8xDBOG/aN62AZKHOe
GcyIdu7yNMMRihGVZCYr8rYiJoKiOzDqOkPkLOPdhtVlgnhowzHDxMHND/E2WA5p
ZHuNM/m0TXt2wTTPL7JH2YC0gPz/BvvSzjksgzU5rLbRyUKQkgU=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIG6DCCBNCgAwIBAgICEAUwDQYJKoZIhvcNAQELBQAwcDELMAkGA1UEBhMCUlUx
PzA9BgNVBAoMNlRoZSBNaW5pc3RyeSBvZiBEaWdpdGFsIERldmVsb3BtZW50IGFu
ZCBDb21tdW5pY2F0aW9uczEgMB4GA1UEAwwXUnVzc2lhbiBUcnVzdGVkIFJvb3Qg
Q0EwHhcNMjQwNzE1MTI1MDQxWhcNMjkwNzE5MTI1MDQxWjBvMQswCQYDVQQGEwJS
VTE/MD0GA1UECgw2VGhlIE1pbmlzdHJ5IG9mIERpZ2l0YWwgRGV2ZWxvcG1lbnQg
YW5kIENvbW11bmljYXRpb25zMR8wHQYDVQQDDBZSdXNzaWFuIFRydXN0ZWQgU3Vi
IENBMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA1j0rkZECOt1S8o7I
JY+4YKAxuEa5xaHKHXT2EpkuC/0krqMOjUy2oPIRNgR5g8X0Jl6jamxeGLc4Q1tf
ju6or9oSRYThIUhRsFDQNBiBBEXoBgWxTfiKB2eyT97+pz5TBtBiRCPaLGRHYLRb
9Jz2HkJlxbtNPjtDrF5DPHym+mZ1M1z3hIQYAqJwLpsEBnsw/VxWMlxqHoeewd0h
uJMd71KQ5vOKlz7KrIZ6EobNNa6wItuvsfj3kYCK7O78uLHGXXFxdr8Hae9lMUmC
8F7AFwa+bO1LRlTlqW7rE3rLf+jj70N01N8T3o22v14YBaFBWQWncAVYD2JuL3tH
252+kdNOERf1fLbLRigJAbd+hOhWYlNf963TFDgnNPliHNIW72SygVBnI2V3JwO1
dp1hVKpK/zt8ziGdHW4gmOLTsH50YKdR4jNqUgQv4wASlKn9OpN6zHYc5G8h86fY
BM+zxE5ikGI+I/vIqBuI0eaDU92AWN/YjFLpu8tMu9kLRSCf1vug6FIfDPWVo7iP
ac/SI2v8jnnpaW7ph/Pz3WkzaG7ZZJsfFs+8dploWc6LOoDtbFBhMdGMxu024msC
0PSjZb5ODXPIaO2NsA7fMiAtZcoK6anTUJh4zOP/stA9qsJGNxdrEmiPXSmBZY/N
Y0wkZgZ6JTDhw7038bPvctkblJkCAwEAAaOCAYswggGHMB0GA1UdDgQWBBR3Pdk5
r0K93FvKduru/c4+YSkwXzAfBgNVHSMEGDAWgBTh0YHlzlpfBKrS6badZrHF+qws
hzAOBgNVHQ8BAf8EBAMCAYYwEgYDVR0TAQH/BAgwBgEB/wIBADCBmAYIKwYBBQUH
AQEEgYswgYgwQAYIKwYBBQUHMAKGNGh0dHA6Ly9udWMtY2RwLnZvc2tob2QucnUv
Y2RwL3Jvb3RjYV9zc2xfcnNhMjAyMi5jcnQwRAYIKwYBBQUHMAKGOGh0dHA6Ly9u
dWMtY2RwLmRpZ2l0YWwuZ292LnJ1L2NkcC9yb290Y2Ffc3NsX3JzYTIwMjIuY3J0
MIGFBgNVHR8EfjB8MDqgOKA2hjRodHRwOi8vbnVjLWNkcC52b3NraG9kLnJ1L2Nk
cC9yb290Y2Ffc3NsX3JzYTIwMjIuY3JsMD6gPKA6hjhodHRwOi8vbnVjLWNkcC5k
aWdpdGFsLmdvdi5ydS9jZHAvcm9vdGNhX3NzbF9yc2EyMDIyLmNybDANBgkqhkiG
9w0BAQsFAAOCAgEAmsINXtQ7wwUWvIeOr80MdJS/5G4xhyZOVEmeUorThquT672y
cCg3XCxc4fwbiZqSSbBqntQ7RtiTAKMYMvBageKoVHbzz+R4jX01tKcTx8cDePrz
dJ73bLNUorE7RU9QsW4KyiUeRmjMDV23AUlEvuQFTwgkHXvbac1BBdPn9CrssQuF
5EGohZKcQPFiAAc4SHbRNhlr7uAwgpc/erzI9EAcvA6BVAXcVKoeGpV01uexUgZ6
St5RP9UmDWNA7T4yVXWJ233N0Q8bl+6AswINQ3PosPu6yQQHQjr65YS06epK+AeI
6j+oGR4xI7EhTQhQvaobnGmX/8QQ7XDRYCP2HXYxiffnn/CfZ/BVyKLYeY1ZipjE
nzqdQIC2+Q3WtY8jsVRQMP38WFRmtsIt5snehnPTs5bKGVIcYzj3o3Ex/K7agEz0
zAJ0JR5ivXZOvNkT0g9x1v+S1IkU3e/nX1a+tpRquMtnHX0L2lXArNHUbaOO9EJt
d57WaIpofV5cVhhwShOgAuBc9UMJF3/n4t4RKiPxtsK8P67gcmphMhslj7AMYrYM
ej2NvQZY4m3ub3CPC/PrTjDONvb+8g5xrKtxBjYqC74HSB4dg9G3WimSDUuP2Su6
G2y2TUeyJuCvCLz289VoO0vg7cNdMobE3KCqAiiNhN2VBFxHAUKmUoRcRdw=
-----END CERTIFICATE-----

NETSCHOOL_CA
cat > "$WORK/targets.tsv" <<'NETSCHOOL_TARGETS'
Алтайский край	https://netschool.edu22.info	https://netschool.edu22.info/api/mobile
Амурская область	https://region.obramur.ru	https://region.obramur.ru/api/mobile
Волгоградская область	http://sgo.volganet.ru	-
Забайкальский край	https://region.zabedu.ru	https://region.zabedu.ru/api/mobile
Калужская область	https://edu.admoblkaluga.ru:444	https://edu.admoblkaluga.ru:444/api/mobile
Камчатский край	https://school.sgo41.ru	https://school.sgo41.ru/api/mobile
Костромская область	https://netschool.eduportal44.ru	https://netschool.eduportal44.ru/api/mobile
Краснодарский край	https://sgo.rso23.ru	https://sgo.rso23.ru/api/mobile
Ленинградская область	https://e-school.obr.lenreg.ru	https://e-school.obr.lenreg.ru/api/mobile
Приморский край	https://sgo.prim-edu.ru	https://sgo.prim-edu.ru/api/mobile
Республика Алтай	https://sgo.altaiobr04.ru	https://sgo.altaiobr04.ru/api/mobile
Республика Бурятия	https://deti.obr03.ru	https://deti.obr03.ru/api/mobile
Республика Ингушетия	https://sgo.edu-ri.ru	https://sgo.edu-ri.ru/api/mobile
Республика Коми	https://giseo.rkomi.ru	https://giseo.rkomi.ru/api/mobile
Республика Марий Эл	https://sgo.mari-el.gov.ru	https://sgo.mari-el.gov.ru/api/mobile
Республика Мордовия	https://sgo.e-mordovia.ru	https://sgo.e-mordovia.ru/api/mobile
Республика Саха (Якутия)	https://sgo.e-yakutia.ru	https://sgo.e-yakutia.ru/api/mobile
Рязанская область	https://e-school.ryazan.gov.ru	https://e-school.ryazan.gov.ru/api/mobile
Самарская область	https://asurso.ru	https://asurso.ru/api/mobile
Сахалинская область	https://netcity.admsakhalin.ru:11111	https://netcity.admsakhalin.ru/api/mobile
Тверская область	https://sgo.tvobr.ru	https://sgo.tvobr.ru/api/mobile
Томская область	https://sgo.tomedu.ru	https://sgo.tomedu.ru/api/mobile
Ульяновская область	https://sgo.cit73.ru	https://sgo.cit73.ru/api/mobile
Челябинская область	https://sgo.edu-74.ru	https://sgo.edu-74.ru/api/mobile
Черноголовка	https://journal.nschg.ru	https://journal.nschg.ru/api/mobile
Чувашская Республика	http://net-school.cap.ru	https://net-school.cap.ru/api/mobile
Ямало-Ненецкий автономный округ	https://sgo.yanao.ru	https://sgo.yanao.ru/api/mobile
ТИ НИЯУ МИФИ	https://net.mephi3.ru	-
NETSCHOOL_TARGETS

probe() {
  local url="$1" kind="$2" base="$3" code rc result version
  # -q отключает ~/.curlrc; --noproxy '*' и --proxy '' отключают прокси curl.
  code=$(/usr/bin/curl -q --silent --show-error --location --max-redirs 3 \
    --proxy '' --noproxy '*' --cacert "$WORK/ca.pem" \
    --connect-timeout 8 --max-time "$TIMEOUT" \
    --output "$base.body" --write-out '%{http_code}' \
    "$url" 2>"$base.error")
  rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$rc" in
      6) result="DNS";; 28) result="Таймаут";;
      35|51|58|60|77) result="SSL";;
      *) result="curl $rc";;
    esac
  elif [ "$kind" = mobile ]; then
    case "$code" in 200|401) result="OK";; *) result="HTTP $code";; esac
  elif [ "$code" != 200 ]; then
    result="HTTP $code"
  else
    version=$(/usr/bin/plutil -extract version raw -o - "$base.body" 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$version" ]; then
      result="OK"
    else
      result="Не ответ API"
    fi
  fi
  printf '%s' "$result"
}

check_region() {
  local n="$1" region="$2" url="$3" mobile="$4" web mob
  web=$(probe "${url%/}/webapi/logindata" web "$WORK/$n.web")
  if [ "$mobile" = "-" ]; then mob="—"
  else mob=$(probe "${mobile%/}/users" mobile "$WORK/$n.mobile"); fi
  printf '%s\t%s\t%s\t%s\n' "$region" "$url" "$web" "$mob" > "$WORK/$n.result"
  printf '%s — веб: %s; мобильное: %s\n' "$region" "$web" "$mob"
}

echo "Проверка напрямую с этого Mac, без прокси curl."
echo "Если включён системный VPN, трафик пойдёт через него."
echo "Вход в аккаунты не выполняется. OK мобильного API = HTTP 200 или 401."
echo "Одновременно проверяется до 6 регионов. Подождите 1–3 минуты."
echo
printf 'Регион\tСервер\tВеб-API\tМобильное API\n' > "$REPORT"
n=0
while IFS=$'\t' read -r region url mobile; do
  if [ -n "$MATCH" ] && [[ "$region" != *"$MATCH"* ]]; then continue; fi
  n=$((n+1))
  check_region "$n" "$region" "$url" "$mobile" &
  if [ $((n % 6)) -eq 0 ]; then wait; fi
done < "$WORK/targets.tsv"
wait
web_ok=0; mob_ok=0; mob_total=0
for ((i=1; i<=n; i++)); do
  if [ ! -f "$WORK/$i.result" ]; then echo "Не получен результат проверки $i"; exit 1; fi
  cat "$WORK/$i.result" >> "$REPORT"
  IFS=$'\t' read -r region url web mob < "$WORK/$i.result"
  [ "$web" != OK ] || web_ok=$((web_ok+1))
  if [ "$mob" != "—" ]; then
    mob_total=$((mob_total+1))
    [ "$mob" != OK ] || mob_ok=$((mob_ok+1))
  fi
done
echo
echo "Доступно: веб-API $web_ok/$n; мобильное API $mob_ok/$mob_total."
echo "Отчёт: $REPORT"
echo "Результат — доступность API, успешный вход в аккаунт не проверяется."
if [ -t 0 ]; then read -r -p "Нажмите Enter, чтобы закрыть… " _; fi
