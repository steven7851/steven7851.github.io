import re
import os
import uuid
import time
import hashlib

from requests.adapters import HTTPAdapter
from subprocess import Popen, PIPE

from streamlink.compat import is_win32
from streamlink.plugin import Plugin
from streamlink.plugin.api import http, validate, useragents
from streamlink.stream import HLSStream, HTTPStream, RTMPStream
from streamlink_cli.main import args

API_URL = "https://www.douyu.com/swf_api/room/{0}?cdn=&nofan=yes&_t={1}&sign={2}"
LAPI_URL = "https://www.douyu.com/lapi/live/getPlay/{0}"
VAPI_URL = "https://v.douyu.com/api/swf/getStreamUrl"
H5JS_URL = "https://shark.douyucdn.cn/app/douyu-liveH5/live/js/h5.js"
API_SECRET = "bLFlashflowlad92"
SHOW_STATUS_ONLINE = 1
SHOW_STATUS_OFFLINE = 2
STREAM_WEIGHTS = {
    "low": 540,
    "medium": 720,
    "high": 900,
    "source": 1080
}

_url_re = re.compile(r"""
    http(s)?://
    (?:
        (?P<subdomain>.+)
        \.
    )?
    douyu.com/
    (?:
        show/(?P<vid>[^/&?]+)|
        (?P<channel>[^/&?]+)
    )
""", re.VERBOSE)

_room_id_re = re.compile(r'"room_id\\*"\s*:\s*(\d+),')
_room_id_alt_re = re.compile(r'data-onlineid=(\d+)')
_index_re = re.compile(r'\?roomIndex=(\d+)')
_channel_re = re.compile(r'ch=([^/&?]+)')
_cdn_re = re.compile(r'cdn=([^/&?]+)')
_sign_re = re.compile(r'sign:\s(.+)\scptl:\s(.+)')
_h5ver_re = re.compile(r't\.VERSION="(.+?)"')
_supern_re = re.compile(r'"super"\s*:\s*""')
_super_re = re.compile(r'"super"\s*:\s*{.+?}')

_room_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "show_status": validate.all(
                validate.text,
                validate.transform(int)
            ),
            "rtmp_url": validate.text,
            "rtmp_live": validate.text,
            "rtmp_multi_bitrate": validate.all(
                validate.any([], {
                    validate.text: validate.text
                }),
                validate.transform(dict)
            ),
            "cdns": validate.all(
                validate.any([], [
                    validate.text
                ])
            )
        })
    },
    validate.get("data")
)

_lapi_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "mixed_url": validate.text,
            "mixed_live": validate.text,
            "rtmp_url": validate.text,
            "rtmp_live": validate.text,
            "is_mixed": bool
        })
    },
    validate.get("data")
)

_vapin_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "thumb_video": {
                validate.optional("high"): {
                    "url": validate.text
                },
                validate.optional("normal"): {
                    "url": validate.text
                }
            }
        })
    },
    validate.get("data")
)

_vapi_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "thumb_video": {
                validate.optional("super"): {
                    "url": validate.text
                },
                validate.optional("high"): {
                    "url": validate.text
                },
                validate.optional("normal"): {
                    "url": validate.text
                }
            }
        })
    },
    validate.get("data")
)


class Douyutv(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    @classmethod
    def stream_weight(cls, stream):
        if stream in STREAM_WEIGHTS:
            return STREAM_WEIGHTS[stream], "douyutv"
        return Plugin.stream_weight(stream)

    def _get_room_json(self, channel, cdn, rate, ver, tt, did, sign, cptl):
        data = {
            "cdn": cdn,
            "rate": rate,
            "ver": ver,
            "tt": tt,
            "did": did,
            "sign": sign,
            "cptl": cptl
        }
        res = http.post(LAPI_URL.format(channel), data=data)
        room = http.json(res, schema=_lapi_schema)
        return room

    def _get_streams(self):
        match = _url_re.match(self.url)
        subdomain = match.group("subdomain")

        http.verify = False
        http.mount('https://', HTTPAdapter(max_retries=99))
        http.headers.update({'User-Agent': useragents.CHROME, 'Referer': self.url})
        dir = os.path.dirname(os.path.abspath(__file__))
        node_modules = os.path.join(dir, 'douyutv')
        os.environ['NODE_PATH'] = node_modules
        did = uuid.uuid4().hex
        env = os.environ.copy()
        self.logger.debug(env['PATH'])
        try:
            Popen(['node', '-v'], stdout=PIPE, stderr=PIPE, env=env).communicate()
        except (OSError, IOError) as err:
            self.logger.info(str(err) + "\n"
                "Please install Node.js first.\n"
                "If you have installed Node.js but still show this message,\n"
                "please reboot computer.")
            if is_win32:
                self.logger.info("If you are using windows portable version,\n"
                    "you can copy node.exe to the same folder as streamlink.exe.")
            return

        if subdomain == 'v':
            vid = match.group("vid")
            tt = int(time.time())
            process = Popen(['node', node_modules + '/douyutv_vsigner.js', str(vid), str(tt), did], stdout=PIPE, stderr=PIPE ,env=env)
            res = process.communicate()[0].decode()
            sign = _sign_re.search(res).group(1)
            data = {
                "vid": vid,
                "did": did,
                "tt": tt,
                "sign": sign
            }
            if args.http_cookie:
                res = http.post(VAPI_URL, data=data)
            else:
                cookie = dict(acf_auth='')
                res = http.post(VAPI_URL, data=data, cookies=cookie)
            if _supern_re.search(res.text):
                self.logger.info("This video has source quality, but need logged-in cookie.\n"
                    "Copy acf_auth value in cookie with option '--http-cookie acf_auth=value'.")
                if is_win32:
                    self.logger.info("If you are using windows version,\n"
                        "The percent symbol '%' in cookie must be modified to '%%' in command-line and batch file.")
            if _super_re.search(res.text):
                room = http.json(res, schema=_vapi_schema)
                yield "source", HLSStream(self.session, room["thumb_video"]["super"]["url"])
                yield "medium", HLSStream(self.session, room["thumb_video"]["high"]["url"])
                yield "low", HLSStream(self.session, room["thumb_video"]["normal"]["url"])
            else:
                room = http.json(res, schema=_vapin_schema)
                try:
                    yield "medium", HLSStream(self.session, room["thumb_video"]["high"]["url"])
                except:
                    pass
                yield "low", HLSStream(self.session, room["thumb_video"]["normal"]["url"])
            return

        channel = match.group("channel")
        try:
            channel = int(channel)
        except ValueError:
            res = http.get(self.url)
            try:
                channel = _room_id_re.search(res.text).group(1)
            except AttributeError:
                room_list = _room_id_alt_re.findall(res.text)
                try:
                    ops = _index_re.search(self.url).group(1)
                    channel = room_list[int(ops)]
                except AttributeError:
                    try:
                        self.logger.info("Available sub-channels: {0}".format(room_list))
                        ops = _channel_re.search(self.url).group(1)
                        channel = room_list[int(ops) - 1]
                    except AttributeError:
                        self.logger.info("You can add '?ch=number' after url to choose channel,\n"
                            "if no query string, default use '?ch=1' for first channel in list.")
                        channel = room_list[0]

        ts = int(time.time() / 60)
        sign = hashlib.md5(("{0}{1}{2}".format(channel, API_SECRET, ts)).encode()).hexdigest()
        res = http.get(API_URL.format(channel, ts, sign))
        room = http.json(res, schema=_room_schema)
        if not room:
            self.logger.info("Not a valid room url.")
            return

        if room["show_status"] != SHOW_STATUS_ONLINE:
            self.logger.info("Stream currently unavailable.")
            return

        cdns = room["cdns"]
        try:
            self.logger.info("Available cdns: {0}".format(cdns))
            cdn = _cdn_re.search(self.url).group(1)
        except AttributeError:
            self.logger.info("You can add '?cdn=CDN_name' after url to choose CDN,\n"
                "if no query string, default use '?cdn=ws' for ws CDN.")
            cdn = "ws"

        h5js = http.get(H5JS_URL)
        ver = _h5ver_re.search(h5js.text).group(1)
        tt = int(time.time() / 60)
        process = Popen(['node', node_modules + '/douyutv1_signer.js', str(channel), str(tt), did], stdout=PIPE, stderr=PIPE, env=env)
        res = process.communicate()[0].decode()
        sign = _sign_re.search(res).group(1)
        cptl = _sign_re.search(res).group(2)
        self.logger.info("Now channel: {0}, CDN: {1}".format(channel, cdn))
        rate = [0, 4, 2, 1]
        quality = ["source", "high", "medium", "low"]
        for i in range(0, 4, 1):
            room = self._get_room_json(channel, cdn, rate[i], ver, tt, did ,sign, cptl)
            url = "{room[rtmp_url]}/{room[rtmp_live]}".format(room=room)
            if 'rtmp:' in url:
                stream = RTMPStream(self.session, {
                    "rtmp": url,
                    "live": True
                })
                yield quality[i], stream
            else:
                yield quality[i], HTTPStream(self.session, url)

        if room["is_mixed"]:
            url = "{room[mixed_url]}/{room[mixed_live]}".format(room=room)
            yield "mix", HTTPStream(self.session, url)


__plugin__ = Douyutv
