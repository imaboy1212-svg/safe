import os
import re
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()  # .env 파일에서 환경변수 로드
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BLOG_ID = os.environ.get("BLOG_ID")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
if not BLOG_ID:
    raise ValueError("BLOG_ID 환경변수가 설정되지 않았습니다.")

token_json_content = os.environ.get("GOOGLE_TOKEN_JSON")
if not token_json_content:
    raise ValueError("GOOGLE_TOKEN_JSON 환경변수가 설정되지 않았습니다.")
with open('token.json', 'w') as f:
    f.write(token_json_content)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
model_name = 'gemini-2.5-flash'
print(f"✅ Using model: {model_name}\n")

TOPICS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'topics.json')

AD_DISPLAY = """
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-6858780475640766"
     crossorigin="anonymous"></script>
<!-- 디스플레이광고 -->
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-6858780475640766"
     data-ad-slot="1825484842"
     data-ad-format="auto"
     data-full-width-responsive="true"></ins>
<script>
     (adsbygoogle = window.adsbygoogle || []).push({});
</script>
"""

AD_MULTIPLEX = """
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-6858780475640766"
     crossorigin="anonymous"></script>
<ins class="adsbygoogle"
     style="display:block"
     data-ad-format="autorelaxed"
     data-ad-client="ca-pub-6858780475640766"
     data-ad-slot="3873632172"></ins>
<script>
     (adsbygoogle = window.adsbygoogle || []).push({});
</script>
"""


def insert_ads(body):
    """첫 번째·두 번째 h2 위에 디스플레이 광고, 본문 맨 아래에 멀티플렉스 광고 삽입"""
    count = 0

    def _prepend_ad(match):
        nonlocal count
        count += 1
        if count <= 2:
            return AD_DISPLAY + match.group(0)
        return match.group(0)

    body = re.sub(r'<h2[^>]*>', _prepend_ad, body)
    body += AD_MULTIPLEX
    return body


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 텔레그램 설정 없음. 전송 건너뜁니다.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("✅ 텔레그램 전송 완료")
        else:
            print(f"⚠️ 텔레그램 전송 실패: {resp.text}")
    except Exception as e:
        print(f"⚠️ 텔레그램 전송 오류: {e}")


def load_topics():
    with open(TOPICS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_topics(data):
    with open(TOPICS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_today_topic(data):
    categories = data['categories']
    topics = data['topics']
    idx = data['current_index']

    if idx < len(topics):
        item = topics[idx]
        return item['category'], item['title']
    else:
        # 사전 등록된 30개 주제 소진 -> 카테고리만 순환시키고 Gemini가 직접 새 주제를 정함
        category = categories[(idx - len(topics)) % len(categories)]
        return category, None


def build_prompt(category, fixed_title, today_str):
    if fixed_title:
        topic_instruction = (
            f'오늘 작성할 주제는 다음과 같이 확정되어 있습니다.\n'
            f'"{fixed_title}"\n'
            f'이 주제를 그대로 다루되, 제목은 클릭을 유도하도록 자연스럽게 다듬어도 됩니다.'
        )
    else:
        topic_instruction = (
            f'사전에 등록된 주제가 모두 소진되었습니다. "{category}" 카테고리에 맞는 '
            f'실용적인 새 주제를 직접 정하여 글을 작성하세요.'
        )

    prompt = f"""
당신은 IT 보안 및 개인정보 보호 전문 웹사이트 'Safe-labs'의 수석 테크니컬 라이터입니다.
오늘 날짜는 {today_str} 입니다. 이 시점 기준으로 내용이 어색하지 않게 작성하세요.

{topic_instruction}

[카테고리]
{category}

[톤앤매너]
- 전문가다운 신뢰감을 주면서도 일반인이 이해하기 쉽게 친절하고 직관적인 어투로 작성하세요.
- "제가 직접 해보니", "제가 다녀와 보니" 같은 직접 경험 표현은 절대 사용하지 마세요.
- 신뢰할 수 있는 온라인 정보와 팩트를 기반으로 체계적으로 정리한 객관적인 가이드 형태로 작성하세요.

[구조 및 SEO 규칙]
- 논리적인 흐름을 위해 <h2>와 <h3> 태그를 적극적으로 활용하세요.
- 스마트폰으로 슥슥 스크롤하며 읽는 독자를 기준으로 쓰세요. 한 문단은 1~2문장, 한 문장은 짧게 끊으세요.
- 긴 설명 문단 대신 소제목 + 목록 중심으로 구성해서, 소제목과 목록만 훑어도 내용이 다 이해되게 하세요.
- 사용자가 따라 해야 하는 단계는 <ol><li> 또는 <ul><li> 로 스캐닝하기 좋게 구성하세요.
- 글머리 번호는 위계에 따라 다르게 사용하세요.
  1) <h2> 제목 앞: 1., 2., 3. 같은 아라비아 숫자를 순서대로 붙이세요. (예: <h2>1. 첫 번째 소제목</h2>)
  2) <h3> 제목 앞: ①, ②, ③ 같은 동그라미 숫자를 순서대로 붙이세요. (예: <h3>① 세부 항목</h3>)
  3) 그보다 하위 목록(본문 중 나열)에는 •, ・ 같은 글머리 기호를 사용하세요.
- 문단 내에서 강조해야 할 핵심 문구는 <strong> 태그로 굵게 처리하거나, <mark>로 형광펜 효과를 주거나, <span style="color:#e63946;">처럼 글자색을 입혀서 눈에 띄게 표시하세요.

[이미지 프롬프트 규칙]
- 각 h2 단락 아래에 어울리는 이미지를 넣을 수 있도록, 다음 형식으로 이미지 프롬프트를 삽입하세요.
  [이미지 프롬프트: (영문 설명)]
- 이미지는 2D 픽토그램 기반의 카드뉴스(card news) 또는 2D 인포그래픽(flat 2D infographic) 스타일로만 묘사하세요. 단락 내용을 한 장으로 요약해주는 그림이어야 합니다.
- 화려하거나 인위적인 AI 그래픽 느낌, 사실적인 사진(Realistic photo), 3D 렌더링 느낌은 배제하세요.
- 이미지 안에 들어가는 모든 텍스트(제목, 라벨, 버튼, 말풍선 등)는 반드시 한국어 한글(Korean Hangul)로 표기하세요. 브랜드명·제품명·서비스명 같은 고유명사만 원어 표기를 허용합니다.
- 이를 위해 영문 이미지 프롬프트 끝에 다음 문구를 항상 포함하세요: "All text in the image must be written in Korean Hangul only, except proper nouns like brand names. No English text."

[필수 구성 요소]
- 매력적인 클릭 유도형 메인 제목
- 인사말이나 잡담 없이 첫 문단부터 바로 핵심 결론(가장 중요한 정보/해결책)을 제시
- 본문: 단계별 해결 방법 안내, 스크린샷이 필요한 위치에는 [이곳에 (설명) 화면 캡처 삽입] 형식으로 표시
- 요약 및 마무리: 핵심 내용 3줄 요약 및 보안 팁 강조
- 배경 설명, 반복, 뻔한 부연은 전부 삭제하세요. 문장마다 "이 문장이 없어도 따라 할 수 있나?"를 기준으로 남기세요.

[출력 형식]
반드시 아래 형식으로만 응답하세요. 다른 설명은 절대 추가하지 마세요.
[TITLE]제목[/TITLE]
이후 본문 HTML만 작성하세요. Markdown 기호(**, #, -, *)는 사용하지 말고 HTML 태그만 사용하세요.
전체 글자 수는 500~800자 내외로, 요점만 딱딱 짚어 전달하세요.
"""
    return prompt


def run_safe_labs_automation():
    print("🚀 Safe-labs 블로그 임시저장 자동화를 시작합니다...")

    creds = Credentials.from_authorized_user_file('token.json')
    blogger_service = build('blogger', 'v3', credentials=creds)

    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    today_str = kst_now.strftime("%Y년 %m월 %d일")

    data = load_topics()
    idx = data['current_index']
    category, fixed_title = get_today_topic(data)

    print(f"📌 오늘의 카테고리 -> {category}")
    if fixed_title:
        print(f"📌 오늘의 주제 -> {fixed_title}")
    else:
        print("📌 사전 등록된 주제 소진 -> Gemini가 직접 주제를 생성합니다")

    prompt = build_prompt(category, fixed_title, today_str)

    print("🤖 제미나이가 글을 작성 중입니다...")
    blog_content = ""
    for attempt in range(5):
        try:
            response = gemini_client.models.generate_content(model=model_name, contents=prompt)
            blog_content = response.text
            print("✅ 제미나이 글 작성 완료!")
            break
        except Exception as e:
            print(f"⚠️ Gemini 호출 실패 ({attempt+1}/5): {e}")
            if attempt < 4:
                wait = 60 * (attempt + 1)
                print(f"  {wait}초 후 재시도...")
                time.sleep(wait)
            else:
                send_telegram("❌ Safe-labs 자동화 실패\nGemini 글 작성에 실패했습니다.")
                raise

    title_match = re.search(r'\[TITLE\](.*?)\[/TITLE\]', blog_content, re.DOTALL)
    title = title_match.group(1).strip() if title_match else (fixed_title or "Safe-labs 보안 가이드")

    body = re.sub(r'\[TITLE\].*?\[/TITLE\]\n?', '', blog_content, flags=re.DOTALL).strip()
    body = insert_ads(body)

    print(f"📝 글 제목 -> {title}")
    print(f"🏷️ 라벨 -> {category}")

    print("🌐 블로그스팟에 임시저장으로 전송하는 중...")
    post_data = {'title': title, 'content': body, 'labels': [category]}
    request = blogger_service.posts().insert(blogId=BLOG_ID, body=post_data, isDraft=True)
    result = request.execute()
    print("🎉 완료! 블로그스팟 관리자 페이지의 '임시 저장물' 보관함에 등록되었습니다.")

    post_url = result.get('url', '블로그 관리자 페이지에서 확인 필요')
    send_telegram(
        f"✅ <b>Safe-labs 임시저장 완료</b>\n\n"
        f"📝 제목 {title}\n"
        f"🏷️ 라벨 {category}\n\n"
        f"🔗 확인 {post_url}"
    )

    # 다음 주제로 인덱스 이동
    data['current_index'] = idx + 1
    save_topics(data)


if __name__ == '__main__':
    run_safe_labs_automation()
