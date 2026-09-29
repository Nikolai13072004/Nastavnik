"""Stage 15-2: the can(user, action, workspace) authorization resolver.

The single decider for "what's allowed". Covered at the function level for
owner / teacher / student / non-member — the security-critical logic. Endpoint
wiring is inert until 15-3 makes a course the active workspace (a personal
workspace's owner may do everything, so existing endpoint tests still pass).
"""

from src import courses
# Models imported at module load so they're registered on Base.metadata before
# the db_session fixture runs create_all.
from src.db_models import User, Workspace, WorkspaceMember


def _user(db, email):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.flush()
    return u


def _course_with_members(db):
    owner = _user(db, "owner@x")
    teacher = _user(db, "teacher@x")
    student = _user(db, "student@x")
    outsider = _user(db, "outsider@x")

    course = Workspace(name="Сети", owner_user_id=owner.id, kind=courses.WORKSPACE_KIND_COURSE)
    db.add(course)
    db.flush()
    db.add(WorkspaceMember(workspace_id=course.id, user_id=teacher.id, role=courses.ROLE_TEACHER))
    db.add(WorkspaceMember(workspace_id=course.id, user_id=student.id, role=courses.ROLE_STUDENT))
    db.commit()
    return course, owner, teacher, student, outsider


def test_role_resolution(db_session):
    course, owner, teacher, student, outsider = _course_with_members(db_session)

    assert courses.role_in_workspace(db_session, owner.id, course) == courses.ROLE_OWNER
    assert courses.role_in_workspace(db_session, teacher.id, course) == courses.ROLE_TEACHER
    assert courses.role_in_workspace(db_session, student.id, course) == courses.ROLE_STUDENT
    assert courses.role_in_workspace(db_session, outsider.id, course) is None


def test_student_can_use_but_not_manage(db_session):
    course, _owner, _teacher, student, _outsider = _course_with_members(db_session)

    assert courses.can(db_session, student, courses.ACTION_VIEW, course)
    assert courses.can(db_session, student, courses.ACTION_CHAT, course)
    assert courses.can(db_session, student, courses.ACTION_SUMMARY, course)
    # A student may take an assigned test (Stage 18) but not manage assignments.
    assert courses.can(db_session, student, courses.ACTION_TAKE_ASSIGNMENT, course)

    for action in (
        courses.ACTION_UPLOAD,
        courses.ACTION_REINDEX,
        courses.ACTION_DELETE_MATERIAL,
        courses.ACTION_MANAGE_MEMBERS,
        courses.ACTION_MANAGE_ASSIGNMENTS,
        courses.ACTION_DELETE_COURSE,
    ):
        assert not courses.can(db_session, student, action, course), action


def test_teacher_manages_materials_and_members_but_not_delete_course(db_session):
    course, _owner, teacher, _student, _outsider = _course_with_members(db_session)

    for action in (
        courses.ACTION_VIEW,
        courses.ACTION_CHAT,
        courses.ACTION_SUMMARY,
        courses.ACTION_UPLOAD,
        courses.ACTION_REINDEX,
        courses.ACTION_DELETE_MATERIAL,
        courses.ACTION_MANAGE_MEMBERS,
        courses.ACTION_MANAGE_ASSIGNMENTS,
        courses.ACTION_TAKE_ASSIGNMENT,
        courses.ACTION_RENAME_COURSE,
        courses.ACTION_MANAGE_JOIN_CODE,
    ):
        assert courses.can(db_session, teacher, action, course), action

    assert not courses.can(db_session, teacher, courses.ACTION_DELETE_COURSE, course)


def test_owner_can_do_everything(db_session):
    course, owner, _teacher, _student, _outsider = _course_with_members(db_session)

    for action in (
        courses.ACTION_VIEW,
        courses.ACTION_CHAT,
        courses.ACTION_SUMMARY,
        courses.ACTION_UPLOAD,
        courses.ACTION_REINDEX,
        courses.ACTION_DELETE_MATERIAL,
        courses.ACTION_MANAGE_MEMBERS,
        courses.ACTION_MANAGE_ASSIGNMENTS,
        courses.ACTION_TAKE_ASSIGNMENT,
        courses.ACTION_RENAME_COURSE,
        courses.ACTION_MANAGE_JOIN_CODE,
        courses.ACTION_DELETE_COURSE,
    ):
        assert courses.can(db_session, owner, action, course), action


def test_non_member_can_do_nothing(db_session):
    course, _owner, _teacher, _student, outsider = _course_with_members(db_session)

    for action in (
        courses.ACTION_VIEW,
        courses.ACTION_CHAT,
        courses.ACTION_SUMMARY,
        courses.ACTION_UPLOAD,
        courses.ACTION_DELETE_MATERIAL,
        courses.ACTION_TAKE_ASSIGNMENT,
        courses.ACTION_MANAGE_ASSIGNMENTS,
        courses.ACTION_DELETE_COURSE,
    ):
        assert not courses.can(db_session, outsider, action, course), action


def test_archived_course_is_read_only(db_session):
    """Stage 22: an archived course allows only ``view`` — even for the owner."""
    course, owner, teacher, student, _outsider = _course_with_members(db_session)
    course.is_archived = True
    db_session.commit()

    for actor in (owner, teacher, student):
        assert courses.can(db_session, actor, courses.ACTION_VIEW, course)
        for action in (
            courses.ACTION_CHAT,
            courses.ACTION_SUMMARY,
            courses.ACTION_STUDY,
            courses.ACTION_UPLOAD,
            courses.ACTION_TAKE_ASSIGNMENT,
            courses.ACTION_MANAGE_ASSIGNMENTS,
            courses.ACTION_DELETE_MATERIAL,
        ):
            assert not courses.can(db_session, actor, action, course), (actor.email, action)


def test_personal_workspace_owner_keeps_full_rights(db_session):
    """Backward compat: a personal workspace has no member row — the owner is
    recognized via owner_user_id, so single-user behavior is unchanged."""
    user = _user(db_session, "solo@x")
    personal = Workspace(name="Personal", owner_user_id=user.id)  # kind defaults to personal
    db_session.add(personal)
    db_session.commit()

    assert courses.role_in_workspace(db_session, user.id, personal) == courses.ROLE_OWNER
    assert courses.can(db_session, user, courses.ACTION_UPLOAD, personal)
    assert courses.can(db_session, user, courses.ACTION_DELETE_MATERIAL, personal)
    assert courses.can(db_session, user, courses.ACTION_CHAT, personal)
