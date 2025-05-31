import streamlit as st
import google.generativeai as genai
import PyPDF2 # PDF 텍스트 추출
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled, CouldNotRetrieveTranscript # 유튜브 스크립트 관련 예외
import pandas as pd # CSV/XLSX 처리
import io # 파일 다운로드를 위한 버퍼
import re # Gemini 응답 파싱을 위한 정규 표현식
import datetime # Copyright 연도 표시용
from io import BytesIO # XLSX 파일 생성을 위한 BytesIO
import requests # 웹사이트 콘텐츠 요청
from bs4 import BeautifulSoup # HTML 파싱

# --- Blooket CSV/XLSX 컬럼 정의 ---
BLOOKET_COLUMNS = [
    "Question #",
    "Question Text",
    "Answer 1",
    "Answer 2",
    "Answer 3",
    "Answer 4",
    "Time Limit (sec)",
    "Correct Answer(s)"
]

# --- Gemini API 설정 ---
try:
    gemini_api_key = st.secrets.get("GEMINI_API_KEY")
    if not gemini_api_key:
        st.error("Gemini API 키가 secrets.toml 파일에 설정되지 않았습니다. 확인해주세요.")
        st.stop()
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash-latest" # 또는 다른 원하는 모델
    )
except AttributeError:
    st.error("Streamlit 버전이 낮아 st.secrets를 지원하지 않을 수 있습니다. 또는 secrets.toml 파일 경로를 확인해주세요.")
    st.stop()
except Exception as e:
    st.error(f"Gemini API 설정 중 오류가 발생했습니다: {e}")
    st.stop()

# --- 1. 콘텐츠 추출 함수 ---
def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text += page.extract_text() or ""
    except Exception as e:
        st.error(f"PDF 텍스트 추출 중 오류 발생: {e}")
        return None
    return text

def get_youtube_transcript(youtube_url):
    video_id = None
    try:
        # 다양한 유튜브 URL 형식에서 비디오 ID 추출
        # 일반적인 watch, shorts, youtu.be, embed 형식
        patterns = [
            r"(?:v=|\/embed\/|\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})",
        ]
        # googleusercontent.com 형식 추가 (주의: 이 형식은 불안정할 수 있음)
        # 예시: youtube.com/watch?v=/VIDEO_ID
        # 예시: youtu.be//VIDEO_ID
        # 예시: youtube.com/shorts//VIDEO_ID (shorts와 유사)
        # youtube.com/watch?v=5 (watch?v= 와 유사)
        
        # googleusercontent URL 패턴들
        # youtube.com/watch?v=/VIDEO_ID
        # youtu.be//VIDEO_ID
        # youtube.com/shorts//VIDEO_ID (shorts)
        # youtube.com/watch?v=5?v=VIDEO_ID (watch)
        # googleusercontent.com/youtube.com/3 (embed) - 이 경우는 /embed/VIDEO_ID 로 변환 후 위 패턴으로 잡힐 수 있음
        # googleusercontent.com/youtube.com//VIDEO_ID (youtu.be)
        
        if "googleusercontent.com/youtube.com/" in youtube_url:
            if "/0/" in youtube_url or "/1/" in youtube_url or "/2/" in youtube_url or "/5/" in youtube_url:
                # .../youtube.com/X/VIDEO_ID... 형태
                match = re.search(r"\/youtube\.com\/[0125]\/([a-zA-Z0-9_-]{11})", youtube_url)
                if match:
                    video_id = match.group(1)
            elif "/3?" in youtube_url: # .../youtube.com/3?v=VIDEO_ID... 형태
                match = re.search(r"v=([a-zA-Z0-9_-]{11})", youtube_url)
                if match:
                    video_id = match.group(1)
            elif "/4/" in youtube_url: # .../youtube.com/4/VIDEO_ID... (embed)
                 match = re.search(r"\/youtube\.com\/4\/([a-zA-Z0-9_-]{11})", youtube_url)
                 if match:
                    video_id = match.group(1)

        if not video_id: # 일반적인 URL 패턴 검사
            for pattern in patterns:
                match = re.search(pattern, youtube_url)
                if match:
                    video_id = match.group(1)
                    break
        
        if not video_id:
            st.error(f"입력하신 URL에서 유튜브 비디오 ID를 추출할 수 없습니다: {youtube_url}")
            return None

        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript_data = None
        # 선호하는 언어 순서: 한국어, 영어
        preferred_languages = ['ko', 'en']
        
        # 수동 스크립트 먼저 시도
        for lang in preferred_languages:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                transcript_data = transcript.fetch()
                st.info(f"'{lang}' 언어의 수동 생성 스크립트를 사용합니다 (비디오 ID: {video_id}).")
                break
            except NoTranscriptFound:
                continue
        
        # 수동 스크립트 없으면 자동 생성 스크립트 시도
        if not transcript_data:
            for lang in preferred_languages:
                try:
                    transcript = transcript_list.find_generated_transcript([lang])
                    transcript_data = transcript.fetch()
                    st.info(f"'{lang}' 언어의 자동 생성 스크립트를 사용합니다 (비디오 ID: {video_id}).")
                    break
                except NoTranscriptFound:
                    continue

        # 그래도 없으면 사용 가능한 첫 번째 스크립트 시도
        if not transcript_data:
            st.warning(f"'{video_id}' 영상에 대해 선호하는 언어(한국어, 영어)의 스크립트를 찾을 수 없습니다. 사용 가능한 첫 번째 스크립트를 시도합니다.")
            available_transcripts = list(transcript_list)
            if available_transcripts:
                transcript_to_fetch = available_transcripts[0]
                transcript_data = transcript_to_fetch.fetch()
                st.info(f"'{transcript_to_fetch.language}' 언어 스크립트를 사용합니다 (비디오 ID: {video_id}).")
            else:
                st.error(f"'{video_id}' 영상에 사용 가능한 스크립트가 전혀 없습니다.")
                return None

        if not transcript_data: # 최종적으로 스크립트 데이터를 얻지 못한 경우
            st.error(f"'{video_id}' 영상에서 스크립트를 가져올 수 없습니다.")
            return None

        return " ".join([item['text'] for item in transcript_data])

    except TranscriptsDisabled:
        st.error(f"해당 영상(ID: {video_id or '알 수 없음'})의 스크립트가 비활성화되어 있습니다.")
        return None
    except NoTranscriptFound: # 이 예외는 위에서 처리되지만, 만약을 위해 남겨둠
        st.error(f"해당 영상(ID: {video_id or '알 수 없음'})에서 요청한 언어의 스크립트를 찾을 수 없습니다.")
        return None
    except CouldNotRetrieveTranscript as e:
        st.error(f"스크립트를 가져오는 중 오류가 발생했습니다 (ID: {video_id or '알 수 없음'}). 유튜브 응답에 문제가 있을 수 있습니다. 오류: {e}")
        return None
    except Exception as e:
        if "no element found" in str(e).lower() or "Unexpected status code 400" in str(e):
            st.error(f"유튜브 스크립트 데이터 파싱 또는 요청 중 오류 발생 (ID: {video_id or '알 수 없음'}). 영상의 스크립트 데이터가 비어있거나, 형식이 잘못되었거나, 접근이 차단되었을 수 있습니다. 다른 영상을 시도해보세요. (오류: {e})")
        else:
            st.error(f"유튜브 스크립트 추출 중 예상치 못한 오류 발생 (ID: {video_id or '알 수 없음'}): {e}")
        print(f"Error fetching transcript for {youtube_url} (video_id: {video_id}): {e}")
        import traceback
        print(traceback.format_exc())
        return None

def extract_text_from_website(url):
    """웹사이트 URL에서 텍스트 콘텐츠를 추출합니다."""
    text = ""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, 'html.parser')

        for element in soup(['script', 'style', 'nav', 'footer', 'aside', 'header', 'form', 'button', 'iframe', 'img', 'a']): # a, img 태그도 내용 추출에서 제외
            element.decompose()
        
        main_content = soup.find('article') or soup.find('main') or soup.find('body')
        
        if main_content:
            # p, div, span 및 제목 태그에서 텍스트 추출 개선
            text_elements = main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'caption', 'blockquote', 'q', 'pre']) # div, span 제외하고 구체적인 태그 명시
            text_parts = []
            for element in text_elements:
                # 클래스 기반 필터링 (예: 광고, 댓글 영역 제외) - 필요시 추가
                # if 'ad' in element.get('class', []) or 'comment' in element.get('class', []):
                #    continue
                paragraph_text = element.get_text(separator=' ', strip=True)
                if paragraph_text and len(paragraph_text.split()) > 3: # 너무 짧은 텍스트는 제외 (단어 3개 이상)
                    text_parts.append(paragraph_text)
            text = "\n\n".join(text_parts) # 문단 구분을 명확히 하기 위해 \n\n 사용
        else:
            text = soup.get_text(separator='\n', strip=True) # 전체 텍스트 추출 시 줄바꿈 기준으로

        if not text.strip():
            st.warning(f"웹사이트에서 유의미한 텍스트 콘텐츠를 추출하지 못했습니다. 페이지 구조를 확인해주세요: {url}")
            return None

    except requests.exceptions.RequestException as e:
        st.error(f"웹사이트 콘텐츠 요청 중 오류 발생: {e}")
        return None
    except Exception as e:
        st.error(f"웹사이트 텍스트 추출 중 오류 발생: {e}")
        return None
    return text


# --- 2. Gemini API로 퀴즈 생성 함수 ---
def generate_quiz_with_gemini(context, num_questions, default_time_limit, difficulty, grade_level):
    difficulty_instruction = ""
    if difficulty == "쉬움":
        difficulty_instruction = "질문과 보기는 명확하고 이해하기 쉽게 작성해주세요. 기본적인 내용을 확인하는 질문 위주로 생성해주세요."
    elif difficulty == "보통":
        difficulty_instruction = "질문은 내용에 대한 이해를 바탕으로 약간의 추론이나 분석을 요구할 수 있습니다. 너무 단순하거나 너무 복잡하지 않은 중간 수준의 질문을 생성해주세요."
    elif difficulty == "어려움":
        difficulty_instruction = "질문은 내용에 대한 깊이 있는 이해와 비판적 사고, 복합적인 분석 능력을 요구해야 합니다. 여러 정보를 종합하거나 숨겨진 의미를 파악해야 하는 질문을 생성해주세요."

    grade_level_instruction = f"대상 학년 수준은 '{grade_level}'입니다. 해당 수준의 어휘와 배경지식을 고려하여 질문과 보기를 작성해주세요."
    if grade_level == "전체 (선택 안 함)":
        grade_level_instruction = "대상은 일반적인 수준의 사용자입니다. 특정 학년에 치우치지 않는 보편적인 어휘와 내용을 사용해주세요."


    prompt = f"""
    당신은 Blooket 게임용 퀴즈를 만드는 전문가입니다. 다음 내용을 바탕으로 객관식 퀴즈 {num_questions}개를 만들어 주세요.
    각 퀴즈는 다음 형식을 반드시 따라야 하며, 각 항목은 다음 줄로 구분해주세요:

    [질문시작]
    질문: [여기에 질문 내용]
    보기1: [여기에 첫 번째 보기]
    보기2: [여기에 두 번째 보기]
    보기3: [여기에 세 번째 보기]
    보기4: [여기에 네 번째 보기]
    정답번호: [1, 2, 3, 또는 4 중 하나]
    시간제한: {default_time_limit}
    [질문끝]

    ---
    [중요 규칙]
    1. "정답번호:" 다음에는 반드시 1, 2, 3, 4 중 하나의 숫자만 적어주세요. 이 숫자는 정답에 해당하는 보기의 번호입니다.
    2. 각 퀴즈는 "[질문시작]"으로 시작하고 "[질문끝]"으로 끝나야 합니다.
    3. 퀴즈와 퀴즈 사이에는 "---" 구분선을 넣어주세요. (마지막 퀴즈 뒤에는 넣지 않아도 됩니다.)
    4. 모든 질문의 "시간제한:"은 {default_time_limit}초로 고정해주세요.
    5. 보기는 서로 다른 내용이어야 합니다.
    6. 제공된 내용과 관련된 질문과 보기만 생성해주세요.

    [퀴즈 내용 지침]
    - 제공된 내용의 **핵심 개념, 주요 아이디어, 중요한 사실, 인물, 사건, 용어의 정의, 핵심 표현**을 중심으로 질문을 만들어주세요.
    - 학습자가 **반드시 알아야 할 내용**이나 **이해도를 평가할 수 있는 내용**을 질문으로 만들어주세요.
    - 내용의 **의미를 이해하고 적용하는 능력**을 평가할 수 있는 질문을 포함해주세요.
    - **단순히 페이지 번호, 문서의 특정 위치, 목차, 또는 매우 지엽적이거나 사소한 세부 정보에 대한 질문은 반드시 피해주세요.**
    - 질문은 내용에 대한 **깊이 있는 이해**를 요구해야 하며, 단순 암기나 표면적인 정보 확인에 그쳐서는 안 됩니다.
    - 예를 들어, "3페이지의 주요 내용은 무엇인가요?" 같은 질문 대신, "이 문서에서 설명하는 [핵심 개념]의 주요 특징은 무엇인가요?" 또는 "[주요 사건]이 발생한 근본적인 원인은 무엇이라고 설명하고 있나요?" 와 같이 구체적이고 심층적인 질문을 생성해주세요.

    [난이도 및 학년 수준 지침]
    - {grade_level_instruction}
    - {difficulty_instruction}
    ---

    내용:
    {context}
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        st.error(f"Gemini API 호출 중 오류 발생: {e}")
        return None

# --- 3. Gemini 응답 파싱 함수 ---
def parse_gemini_response(response_text, default_time_limit):
    quiz_items = []
    if not response_text:
        return quiz_items
    question_blocks = re.findall(r"\[질문시작\](.*?)\[질문끝\]", response_text, re.DOTALL)
    question_number_counter = 1

    for block in question_blocks:
        block = block.strip()
        item = {
            BLOOKET_COLUMNS[0]: question_number_counter, # "Question #"
            BLOOKET_COLUMNS[1]: "", # "Question Text"
            BLOOKET_COLUMNS[2]: "", # "Answer 1"
            BLOOKET_COLUMNS[3]: "", # "Answer 2"
            BLOOKET_COLUMNS[4]: "", # "Answer 3"
            BLOOKET_COLUMNS[5]: "", # "Answer 4"
            BLOOKET_COLUMNS[7]: "", # "Correct Answer(s)" (정답 번호)
            BLOOKET_COLUMNS[6]: default_time_limit  # "Time Limit (sec)"
        }

        try:
            q_match = re.search(r"질문:\s*(.+)", block)
            o1_match = re.search(r"보기1:\s*(.+)", block)
            o2_match = re.search(r"보기2:\s*(.+)", block)
            o3_match = re.search(r"보기3:\s*(.+)", block)
            o4_match = re.search(r"보기4:\s*(.+)", block)
            ans_num_match = re.search(r"정답번호:\s*([1-4])", block)
            time_match = re.search(r"시간제한:\s*(\d+)", block)

            if q_match: item[BLOOKET_COLUMNS[1]] = q_match.group(1).strip()
            if o1_match: item[BLOOKET_COLUMNS[2]] = o1_match.group(1).strip()
            if o2_match: item[BLOOKET_COLUMNS[3]] = o2_match.group(1).strip()
            if o3_match: item[BLOOKET_COLUMNS[4]] = o3_match.group(1).strip()
            if o4_match: item[BLOOKET_COLUMNS[5]] = o4_match.group(1).strip()

            correct_answer_number = ""
            if ans_num_match:
                correct_answer_number = int(ans_num_match.group(1).strip())
            item[BLOOKET_COLUMNS[7]] = correct_answer_number

            if time_match:
                item[BLOOKET_COLUMNS[6]] = int(time_match.group(1).strip())

            if all(item[col] != "" for col in [BLOOKET_COLUMNS[1], BLOOKET_COLUMNS[2], BLOOKET_COLUMNS[3], BLOOKET_COLUMNS[4], BLOOKET_COLUMNS[5]]) and \
               isinstance(item[BLOOKET_COLUMNS[7]], int) and \
               1 <= item[BLOOKET_COLUMNS[7]] <= 4:
                quiz_items.append(item)
                question_number_counter += 1
            else:
                st.warning(f"다음 퀴즈 블록 파싱 실패 또는 필수 정보 누락/정답 번호 오류 (내부 번호 {question_number_counter} 건너뜀):\n{block[:150]}...")
        except Exception as e:
            st.warning(f"퀴즈 블록 파싱 중 오류 발생 (내부 번호 {question_number_counter} 건너뜀): {e}\n블록 내용: {block[:150]}...")
            continue

    if not quiz_items and response_text:
        st.warning("Gemini 응답에서 유효한 퀴즈 형식을 찾지 못했습니다. Gemini 원본 응답을 확인해주세요.")
    return quiz_items

# --- 4. 파일 변환 함수 ---
def convert_to_blooket_csv(quiz_data_list):
    if not quiz_data_list:
        return None
    df = pd.DataFrame(quiz_data_list, columns=BLOOKET_COLUMNS)
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    return csv_buffer.getvalue()

def convert_to_blooket_xlsx(quiz_data_list):
    if not quiz_data_list:
        return None
    df = pd.DataFrame(quiz_data_list, columns=BLOOKET_COLUMNS)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Blooket Quiz')
    return output.getvalue()

# --- Streamlit UI 구성 ---
st.set_page_config(page_title="Blooket 퀴즈 생성기", layout="wide", initial_sidebar_state="expanded")
st.title("📝 Blooket 퀴즈 생성 마법사 ✨")
st.markdown("PDF, 텍스트, 유튜브 영상 또는 웹사이트 링크를 입력하면, Gemini AI가 Blooket용 퀴즈 파일을 뚝딱 만들어 드립니다!")
st.markdown("---")

with st.sidebar:
    st.header("1. 콘텐츠 입력 방식 선택")
    input_type = st.radio(
        "퀴즈를 만들고 싶은 콘텐츠 유형을 선택하세요:",
        ('텍스트 직접 입력', 'PDF 파일 업로드', '유튜브 URL', '웹사이트 URL'), # '유튜브 URL' 다시 추가
        key="input_type_radio"
    )
    st.markdown("---")
    st.header("2. 퀴즈 생성 옵션")
    num_questions = st.number_input("생성할 질문 수:", min_value=1, max_value=30, value=5, step=1, key="num_q_input")
    default_time_limit = st.number_input("질문 당 기본 시간 제한 (초):", min_value=5, max_value=300, value=20, step=5, key="time_limit_input")

    st.markdown("---")
    st.header("3. 수준 설정 (선택 사항)")
    difficulty_options = ["선택 안 함", "쉬움", "보통", "어려움"]
    difficulty = st.selectbox("문항 난이도:", difficulty_options, index=0, key="difficulty_select")

    grade_level_options = [
        "전체 (선택 안 함)",
        "초등학교 1-2학년", "초등학교 3-4학년", "초등학교 5-6학년",
        "중학교 1학년", "중학교 2학년", "중학교 3학년",
        "고등학교 1학년", "고등학교 2학년", "고등학교 3학년",
        "대학생", "일반 성인"
    ]
    grade_level = st.selectbox("대상 학년/수준:", grade_level_options, index=0, key="grade_level_select")


source_content = None
uploaded_file_name_prefix = "blooket_quiz"

if input_type == '텍스트 직접 입력':
    st.subheader("텍스트 직접 입력")
    source_content = st.text_area("퀴즈를 만들 내용을 여기에 붙여넣으세요:", height=250, key="text_input_area", placeholder="예시: 대한민국의 수도는 서울입니다...")
    if source_content: uploaded_file_name_prefix = "text_based_quiz"
elif input_type == 'PDF 파일 업로드':
    st.subheader("PDF 파일 업로드")
    uploaded_file = st.file_uploader("퀴즈를 생성할 PDF 파일을 선택하세요.", type="pdf", key="pdf_uploader_widget")
    if uploaded_file:
        with st.spinner(f"'{uploaded_file.name}' 파일에서 텍스트를 추출하는 중..."):
            source_content = extract_text_from_pdf(uploaded_file)
            if source_content:
                 uploaded_file_name_prefix = uploaded_file.name.split('.')[0].replace(" ", "_") + "_quiz"
        if source_content:
            st.success(f"✅ '{uploaded_file.name}'에서 텍스트 추출 완료! (약 {len(source_content):,}자)")
            with st.expander("추출된 PDF 텍스트 미리보기 (일부)"):
                st.text_area("", value=source_content[:2000] + ("..." if len(source_content) > 2000 else ""), height=150, disabled=True)
        elif source_content is None and uploaded_file:
             pass
elif input_type == '유튜브 URL': # 유튜브 URL 입력 로직
    st.subheader("유튜브 영상 URL 입력")
    youtube_url_input = st.text_input("유튜브 영상 URL을 입력하세요:", key="youtube_url_input_field", placeholder="예: https://www.youtube.com/watch?v=...")
    if youtube_url_input:
        with st.spinner(f"'{youtube_url_input}' 영상의 스크립트를 가져오는 중..."):
            source_content = get_youtube_transcript(youtube_url_input)
        if source_content:
            st.success(f"✅ 유튜브 영상 스크립트 가져오기 완료! (약 {len(source_content):,}자)")
            with st.expander("추출된 스크립트 미리보기 (일부)"):
                st.text_area("", value=source_content[:2000] + ("..." if len(source_content) > 2000 else ""), height=150, disabled=True)
            uploaded_file_name_prefix = "youtube_transcript_quiz"
        elif source_content is None and youtube_url_input:
            pass
elif input_type == '웹사이트 URL': # 웹사이트 URL 입력 로직
    st.subheader("웹사이트 URL 입력")
    website_url_input = st.text_input("퀴즈를 생성할 웹사이트 URL을 입력하세요:", key="website_url_input_field", placeholder="예: https://ko.wikipedia.org/wiki/대한민국")
    if website_url_input:
        with st.spinner(f"'{website_url_input}' 웹사이트에서 콘텐츠를 가져오는 중..."):
            source_content = extract_text_from_website(website_url_input)
        if source_content:
            st.success(f"✅ 웹사이트 콘텐츠 가져오기 완료! (약 {len(source_content):,}자)")
            with st.expander("추출된 웹사이트 텍스트 미리보기 (일부)"):
                st.text_area("", value=source_content[:2000] + ("..." if len(source_content) > 2000 else ""), height=150, disabled=True)
            try:
                domain_name = website_url_input.split('//')[-1].split('/')[0].replace("www.", "")
                uploaded_file_name_prefix = re.sub(r'[^a-zA-Z0-9_]', '', domain_name) + "_website_quiz"
            except:
                uploaded_file_name_prefix = "website_content_quiz"
        elif source_content is None and website_url_input:
            pass


st.markdown("---")
if st.button("🚀 Blooket 퀴즈 생성 시작!", type="primary", use_container_width=True, disabled=(not source_content)):
    if source_content:
        st.markdown("---")
        st.subheader("⏳ 퀴즈 생성 중...")
        progress_bar = st.progress(0, text="Gemini AI와 통신 중...")

        gemini_output = generate_quiz_with_gemini(source_content, num_questions, default_time_limit, difficulty, grade_level)
        progress_bar.progress(50, text="Gemini AI 응답 분석 중...")

        if gemini_output:
            with st.expander("🤖 Gemini API 응답 원본 보기", expanded=False):
                st.text_area("API Response:", value=gemini_output, height=200, key="gemini_raw_output_area")
            parsed_quiz_data = parse_gemini_response(gemini_output, default_time_limit)
            progress_bar.progress(80, text="퀴즈 데이터 파싱 및 파일 준비 중...")

            if parsed_quiz_data:
                st.subheader("📊 생성된 퀴즈 미리보기")
                df_preview = pd.DataFrame(parsed_quiz_data)
                st.dataframe(df_preview, use_container_width=True)
                progress_bar.progress(100, text="퀴즈 생성 완료! 파일을 다운로드하세요.")
                st.balloons()
                st.success("🎉 Blooket용 퀴즈 파일 생성이 성공적으로 완료되었습니다!")

                final_base_filename = f"{re.sub(r'[^a-zA-Z0-9_]', '', uploaded_file_name_prefix)}_{num_questions}q"
                if difficulty != "선택 안 함":
                    final_base_filename += f"_{difficulty.replace(' ', '_')}"
                if grade_level != "전체 (선택 안 함)":
                    grade_level_filename_part = grade_level.replace(' ', '_').replace('-', '')
                    final_base_filename += f"_{grade_level_filename_part}"


                col1, col2 = st.columns(2)

                with col1:
                    csv_data = convert_to_blooket_csv(parsed_quiz_data)
                    if csv_data:
                        st.download_button(
                            label="📥 CSV 파일 다운로드 (.csv)",
                            data=csv_data,
                            file_name=f"{final_base_filename}.csv",
                            mime="text/csv",
                            use_container_width=True,
                            key="csv_download_button"
                        )
                    else:
                        st.error("CSV 파일 데이터가 없습니다.")

                with col2:
                    xlsx_data = convert_to_blooket_xlsx(parsed_quiz_data)
                    if xlsx_data:
                        st.download_button(
                            label="📥 XLSX 파일 다운로드 (.xlsx)",
                            data=xlsx_data,
                            file_name=f"{final_base_filename}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key="xlsx_download_button"
                        )
                    else:
                        st.error("XLSX 파일 데이터가 없습니다.")

                st.info(f"""
                **다운로드된 파일 사용법:**
                1. Blooket 웹사이트에 로그인합니다.
                2. 'Create' 또는 'My Sets'로 이동하여 새 퀴즈 세트를 만듭니다.
                3. 'Create Method'에서 'CSV Import' 또는 유사한 옵션을 선택합니다.
                4. 다운로드한 CSV 또는 XLSX 파일을 업로드합니다.
                5. Blooket의 컬럼명과 파일의 컬럼명을 **정확히** 매칭시킵니다.
                   (예: 파일의 "{BLOOKET_COLUMNS[1]}" -> Blooket의 "Question", 파일의 "{BLOOKET_COLUMNS[7]}" -> Blooket의 정답 번호 입력 필드)
                   파일의 "{BLOOKET_COLUMNS[0]}" ({BLOOKET_COLUMNS[0]})은 Blooket에서 순서 확인용으로 사용하거나 무시될 수 있습니다.
                6. 퀴즈 세트 생성을 완료합니다!
                """)
            else:
                progress_bar.empty()
                st.error("❌ Gemini 응답에서 유효한 퀴즈 데이터를 파싱하지 못했습니다.")
        else:
            progress_bar.empty()
            st.error("❌ Gemini로부터 퀴즈를 생성하지 못했습니다.")
    else:
        st.warning("⚠️ 퀴즈를 생성할 콘텐츠가 없습니다.")

st.markdown("---")
current_year = datetime.date.today().year
st.markdown(f"<div style='text-align: center; color: grey;'>This app is made by SH (<a href='https://litt.ly/4sh.space' target='_blank'>litt.ly/4sh.space</a>) © {current_year}</div>", unsafe_allow_html=True)