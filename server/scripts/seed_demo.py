# server/scripts/seed_demo.py
"""8개 핵심 테이블 데모 시드 데이터 주입 스크립트."""

from sqlalchemy.orm import Session
from ontology_map.db.session import get_engine
from ontology_map.db.schema import (
    Classification,
    Relation,
    Node,
    Edge,
    NodeInsight,
    NodeQAPair,
)


def seed():
    with Session(get_engine()) as session:
        # 1. 분류 (Classifications) 생성
        comp = Classification(code="COMPANY", display_name="Company", description="영리 기업")
        tech = Classification(code="TECH", display_name="Technology", description="소프트웨어/기술 모델")
        person = Classification(code="PERSON", display_name="Person", description="인물")
        session.add_all([comp, tech, person])
        session.flush()

        # 2. 관계 (Relations) 정의
        rel_invest = Relation(code="INVESTS_IN", display_name="Invests In")
        rel_develop = Relation(code="DEVELOPS", display_name="Develops")
        rel_leads = Relation(code="LEADS", display_name="Leads")
        session.add_all([rel_invest, rel_develop, rel_leads])
        session.flush()

        # 3. 노드 (Nodes) 생성
        node_openai = Node(
            name="OpenAI",
            classification_id=comp.id,
            description="인공지능 연구 및 배포 기업",
            properties={"country": "USA", "founded": 2015},
        )
        node_ms = Node(
            name="Microsoft",
            classification_id=comp.id,
            description="글로벌 빅테크 기업",
            properties={"country": "USA", "founded": 1975},
        )
        node_gpt = Node(
            name="GPT-5",
            classification_id=tech.id,
            description="차세대 대규모 멀티모달 파운데이션 모델",
            properties={"modality": "multimodal"},
        )
        node_altman = Node(
            name="Sam Altman",
            classification_id=person.id,
            description="OpenAI의 CEO",
            properties={"role": "CEO"},
        )
        session.add_all([node_openai, node_ms, node_gpt, node_altman])
        session.flush()

        # 4. 엣지 (Edges) 연결 (source_node_id, target_node_id 사용)
        edge1 = Edge(
            source_node_id=node_ms.id,
            target_node_id=node_openai.id,
            relation_id=rel_invest.id,
            properties={"weight": 1.0},
        )
        edge2 = Edge(
            source_node_id=node_openai.id,
            target_node_id=node_gpt.id,
            relation_id=rel_develop.id,
            properties={"weight": 1.0},
        )
        edge3 = Edge(
            source_node_id=node_altman.id,
            target_node_id=node_openai.id,
            relation_id=rel_leads.id,
            properties={"weight": 1.0},
        )
        session.add_all([edge1, edge2, edge3])

        # 5. OpenAI(노드 1)의 AI 인사이트 및 Q&A 주입
        insight_openai = NodeInsight(
            node_id=node_openai.id,
            recent_history_summary="마이크로소프트와의 대규모 파트너십을 확장하고 차세대 인공지능 프론티어 모델 개발에 박차를 가하고 있음.",
            overall_insight="생성형 AI 생태계의 선두 주자로서 글로벌 컴퓨팅 인프라 확보 및 상용화 파이프라인 고도화에 주력 중.",
            issues=[
                {"title": "연산 인프라 수급", "severity": "HIGH"},
                {"title": "데이터 저작권 분쟁", "severity": "MEDIUM"},
                {"title": "안전성 평가 규제", "severity": "LOW"},
            ],
        )
        qa1 = NodeQAPair(
            node_id=node_openai.id,
            sequence=1,
            question="주요 투자자는 누구인가요?",
            answer="Microsoft가 다년간 수십억 달러 규모의 전략적 투자를 진행하고 클라우드 인프라(Azure)를 공급하고 있습니다.",
        )
        qa2 = NodeQAPair(
            node_id=node_openai.id,
            sequence=2,
            question="현재 가장 주력하는 R&D 영역은 무엇인가요?",
            answer="추론 능력이 강화된 차세대 파운데이션 모델(GPT 시리즈) 및 자율 에이전트 아키텍처 연구입니다.",
        )
        session.add_all([insight_openai, qa1, qa2])

        session.commit()
        print("✅ 데모 데이터 시딩 완료! (Node IDs: OpenAI=1, Microsoft=2, GPT-5=3, Sam Altman=4)")


if __name__ == "__main__":
    seed()
