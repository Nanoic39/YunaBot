"""首批服务目录与 DeepSeek 只读余额接口；没有通用代扣接口。"""
from decimal import Decimal, InvalidOperation

import httpx

PROVIDERS = [
    {'id': 'xiaoheihe', 'label': '小黑盒', 'capabilities': ['app.use', 'social.publish'], 'status': 'planned',
     'note': '社区发帖路径待验证；黑盒语音机器人接口不等同于社区发帖接口。'},
    {'id': 'bilibili', 'label': '哔哩哔哩', 'capabilities': ['app.use', 'social.publish'], 'status': 'planned',
     'note': '动态与视频投稿分别接入；先确认开放平台应用权限和账号授权。'},
    {'id': 'deepseek', 'label': 'DeepSeek Platform', 'capabilities': ['app.use', 'payment.recharge'], 'status': 'balance_only',
     'note': '已实现按需查询官方余额；自动充值接口未接入。'},
    {'id': 'pinduoduo', 'label': '拼多多', 'capabilities': ['app.use', 'shopping.gift'], 'status': 'placeholder',
     'note': '赠礼功能最后推进，当前仅占位。'},
    {'id': 'taobao', 'label': '淘宝', 'capabilities': ['app.use', 'shopping.gift'], 'status': 'placeholder',
     'note': '赠礼功能最后推进，当前仅占位。'},
    {'id': 'alipay', 'label': '支付宝（支付适配器）', 'capabilities': ['app.use', 'payment.pay', 'payment.recharge', 'shopping.gift'], 'status': 'planned',
     'note': '支付路径待确认；商户收款 API 不等同于个人账号任意免密付款。'},
]


async def deepseek_balance(api_key: str, *, transport=None) -> dict:
    if not api_key:
        raise ValueError('未配置 DeepSeek 官方 API Key')
    # 固定官方地址、不跟随重定向，防止将密钥发送给自定义或第三方接口。
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, transport=transport) as client:
        response = await client.get('https://api.deepseek.com/user/balance',
                                    headers={'Authorization': 'Bearer ' + api_key})
    if response.status_code != 200:
        raise ValueError(f'DeepSeek 余额查询失败（HTTP {response.status_code}）')
    data = response.json()
    result = []
    for item in data.get('balance_infos', []):
        currency = str(item.get('currency', ''))
        if currency not in ('CNY', 'USD'):
            raise ValueError('余额响应币种无效')
        row = {'currency': currency}
        for key in ('total_balance', 'granted_balance', 'topped_up_balance'):
            try:
                value = Decimal(str(item[key]))
                if not value.is_finite():
                    raise ValueError('余额响应金额无效')
                row[key] = str(value)
            except (KeyError, InvalidOperation) as error:
                raise ValueError('余额响应金额无效') from error
        result.append(row)
    return {'is_available': bool(data.get('is_available')), 'balances': result, 'read_only': True}
