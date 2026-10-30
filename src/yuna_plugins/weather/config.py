from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关
    weather_enabled: bool = True
    # 数据源：qweather（和风天气，默认）/ wttr
    weather_provider: str = "qweather"
    # 和风天气 API Key（控制台-项目管理-凭据 中生成的「API KEY」），在 .env 中配置
    weather_qweather_api_key: str = ""
    # 和风天气私有化 Host（控制台-设置 中的 API Host）
    weather_qweather_host: str = "jv5g76hr42.re.qweatherapi.com"
    # wttr.in 数据源模板（备用），{city} 会被替换
    weather_wttr_url: str = "https://wttr.in/{city}?format=j1&lang=zh"
    # 默认城市（未指定城市时使用）
    weather_default_city: str = "上海"
    # 请求超时（秒）
    weather_timeout: int = 10
