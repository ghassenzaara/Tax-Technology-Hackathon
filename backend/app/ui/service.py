"""Klaris contract operations, using the existing workflow and repository guards."""

import asyncio
import logging
import shutil
from datetime import UTC, date, datetime

from app.api.deps import Services, company
from app.api.errors import ApiError, ErrorCode
from app.db.repository import BusyError, NotFoundError, StaleDraftError, StateError
from app.mail.provider import DeliveryUncertain, MailError, make_provider
from app.mail.validate import address, mail_text
from app.models.audit import AuditEntry, AuditFilter, AuditKind
from app.models.client import ClientInfo, MatchReview, MultiSendRequest, OutreachResponse
from app.models.outreach import DEFAULT_BODY, DEFAULT_SUBJECT
from app.models.project import EMBEDDABLE
from app.models.review import ConfirmRequest
from app.models.run import Run
from app.models.update import UpdateRequest
from app.profile.review import apply_decisions, draft_version
from app.runs.jobs import LAW_UPLOADS
from app.runs.upload import UPLOADS
from app.services.cleanup import checked_paths, cleanup_targets
from app.services.confirm import confirm_profile, review_profile
from app.services.email import SendFailed, SendUncertain
from app.ui import contract as ui
from app.ui import mapping as m
from app.ui import templates as t

log = logging.getLogger(__name__)


class UiService:
    def __init__(self, services: Services):
        self.s = services
        self.repo = services.repository

    def audit(self, cid: str, kind: AuditKind, **values) -> AuditEntry:
        return AuditEntry(
            company_id=cid,
            kind=kind,
            acted_by=self.s.settings.advisor_label,
            screened_at=datetime.now(UTC),
            **values,
        )

    async def advisor(self) -> ui.Advisor:
        settings = self.s.settings
        return ui.Advisor(
            name=settings.sender_name,
            salutation=settings.sender_salutation,
            title=settings.advisor_label,
            firm=settings.sender_firm,
            seen_law_change_ids=[str(x) for x in await self.repo.list_seen_laws()],
        )

    async def get_company(self, cid: str) -> ui.Company:
        state, runs = await company(self.s, cid)
        active = state is not None and state.state in EMBEDDABLE
        profile = (
            await self.repo.read_ui_profile(cid, self.s.schema, self.s.settings) if active else None
        )
        info = await self.repo.get_client_info(cid)
        times = [
            x
            for x in [
                *(r.created_at for r in runs),
                *(r.finished_at for r in runs),
                state.published_at if state else None,
                state.confirmed_at if state else None,
                info.updated_at if info else None,
                *((e.acted_at for e in profile.audit) if profile else ()),
            ]
            if x
        ]
        name = (
            (profile.scalars.get("name") if profile else None)
            or (runs[0].project_name if runs else None)
            or cid
        )
        result = m.build_company(
            cid,
            name=name,
            status="active" if active else "draft",
            created_at=min(times, default=None),
            updated_at=max(times, default=None),
            confirmed_at=state.confirmed_at if state else None,
            profile=profile,
            schema=self.s.schema,
            info=info,
            merkmal_names=await self.repo.merkmal_names(),
        )
        if profile:
            result.extra["profileVersion"] = profile.profile_version
            result.extra["coreFacts"] = [
                f.model_dump()
                for f in m.company_facts(self.s.schema, profile, info, {}, include_core=True)
                if f.core_field
            ]
        return result

    async def companies(self, drafts: bool = False) -> list[ui.Company]:
        ids = {r.company_id for r in await self.repo.list_runs()} | {
            s.company_id for s in await self.repo.list_project_states()
        }
        values = [await self.get_company(cid) for cid in sorted(ids)]
        return [c for c in values if (c.status == "draft") == drafts]

    async def idle(self, cid: str) -> None:
        _, runs = await company(self.s, cid)
        if self.s.runs.is_active(cid) or any(r.state.active for r in runs):
            raise BusyError("Die Dokumente werden noch verarbeitet.")
        busy = await self.repo.company_busy(cid, self.s.settings.step_lock_timeout_s)
        if busy:
            raise BusyError(f"Verarbeitung läuft: {busy}")

    async def update(self, cid: str, body: ui.CompanyUpdate) -> ui.Company:
        await self.idle(cid)
        profile = await self.repo.read_ui_profile(cid, self.s.schema, self.s.settings)
        core = body.core.model_dump(exclude_unset=True, by_alias=True) if body.core else {}
        contact = core.pop("contact", {}) or {}
        for key, value in contact.items():
            core[f"contact.{key}"] = value
        changes = m.profile_changes(
            self.s.schema, profile, {f.id: f.value for f in body.facts}, core
        )
        info = await self.repo.get_client_info(cid) or ClientInfo(company_id=cid)
        audit = []
        info_patch = {}
        for key, attr in {
            "customerNumber": "customer_number",
            "clientSince": "client_since",
            "contact.salutation": "contact_salutation",
            "contact.name": "contact_name",
            "contact.phone": "contact_phone",
        }.items():
            if key in core and getattr(info, attr) != core[key]:
                audit.append(
                    self.audit(
                        cid,
                        AuditKind.PROFILE_CHANGED,
                        table_name="client_info",
                        field=key,
                        old_value=getattr(info, attr),
                        new_value=core[key],
                    )
                )
                setattr(info, attr, core[key])
                info_patch[attr] = core[key]
        # Even a client-info-only update checks the version the advisor actually saw.
        from app.db.repository import StaleProfileError

        if body.profile_version != profile.profile_version:
            raise StaleProfileError("Das Profil wurde geändert. Bitte neu laden.")
        request = (
            UpdateRequest(
                company_id=cid,
                profile_version=body.profile_version,
                changed_by=self.s.settings.advisor_label,
                changes=changes,
            )
            if changes
            else None
        )
        result = await self.repo.apply_ui_update(
            cid,
            body.profile_version,
            request,
            info_patch,
            audit,
            self.s.schema,
            self.s.settings,
            date.today(),
        )
        await self.repo.remove_profile_sources(cid)
        if result and result.embeddings_stale:
            await self.refresh(cid, set(result.changed_features))
        return await self.get_company(cid)

    async def refresh(self, cid: str, changed: set[str]) -> None:
        try:
            await self.s.jobs.submit_refresh(cid, changed)
        except Exception:
            log.exception("refresh submission failed; recovery available for %s", cid)

    async def note(self, cid: str, notes: str) -> ui.Company:
        await company(self.s, cid)
        await self.repo.save_client_note(cid, notes, self.s.settings.advisor_label)
        return await self.get_company(cid)

    async def draft(self, cid: str, *, automatic: bool = False) -> ui.ProfileDraft:
        if not automatic:
            await self.idle(cid)
        state, runs = await company(self.s, cid)
        if state and state.state in EMBEDDABLE and (not runs or runs[0].started_by != "update_dd"):
            raise StateError("Der Erstentwurf ist bestätigt. Keine neuen Dokumente zu prüfen.")
        review = await review_profile(self.repo, self.s.schema, self.s.settings, cid)
        if runs and (str(runs[0].state) != "finished" or not runs[0].published):
            raise StateError("Die Verarbeitung hat keinen prüfbaren Entwurf erzeugt.")
        names = await self.repo.merkmal_names()
        version = review.draft_version
        if state and state.state in EMBEDDABLE:
            profile = await self.repo.read_ui_profile(cid, self.s.schema, self.s.settings)
            facts = m.update_draft_facts(review.draft, self.s.schema, profile, names)
            version = f"{version}:{profile.profile_version}"
        else:
            facts = m.draft_facts(review.draft, self.s.schema, names)
        return ui.ProfileDraft(
            company_id=cid,
            run_id=runs[0].run_id if runs else "draft",
            created_at=m.iso(review.draft.created_at),
            facts=facts,
            draft_version=version,
        )

    async def auto_save(self, run: Run) -> None:
        """UI uploads save their selected values; missing fields remain empty and editable."""
        if not run.published or run.started_by not in {"ui_upload", "update_dd", "ui_rerun"}:
            return
        cid = run.company_id
        shown = await self.draft(cid, automatic=True)
        backend = await self.repo.get_profile_draft(cid)
        if backend is None:
            raise StateError("Keine Profildaten zum Speichern vorhanden.")
        results = {}
        for field in backend.fields:
            spec = self.s.schema.field(field.name)
            if spec.is_list:
                results.update({m.row_fact_id(spec, item.key): item for item in field.items})
            else:
                results[field.name] = field
        resolutions = {}
        for fact in shown.facts:
            if fact.candidates:
                preferred = results[fact.id].suggestion
                candidate = fact.candidates[preferred if preferred is not None else 0]
                resolutions[fact.id] = ui.Resolution(
                    kind="value", value=candidate.value, candidate_id=candidate.id
                )
            elif fact.value is None:
                resolutions[fact.id] = ui.Resolution(kind="left_empty")
            else:
                resolutions[fact.id] = ui.Resolution(kind="value", value=fact.value)
        await self.confirm(
            cid,
            ui.ConfirmBody(
                draft_version=shown.draft_version,
                resolutions=resolutions,
            ),
            automatic=True,
        )

    async def confirm(
        self, cid: str, body: ui.ConfirmBody, *, automatic: bool = False
    ) -> ui.Company:
        shown = await self.draft(cid, automatic=automatic)
        review = await review_profile(self.repo, self.s.schema, self.s.settings, cid)
        if body.draft_version != shown.draft_version:
            raise StaleDraftError("Der Entwurf wurde geändert. Bitte neu laden.")
        # Update-DD ignores missing/unchanged fields; only visible differences are reviewed.
        visible = {f.id for f in shown.facts}
        state = await self.repo.get_project_state(cid)
        update_dd = state is not None and state.state in EMBEDDABLE
        if update_dd:
            review.open_decisions = [
                o
                for o in review.open_decisions
                if (
                    m.row_fact_id(self.s.schema.field(o.field), o.key)
                    if o.key is not None
                    else o.field
                )
                in visible
            ]
        for fact in shown.facts:
            if fact.needs_review and fact.id not in body.resolutions:
                raise m.UnresolvedFields([fact.label])
        decisions = m.decisions_from_resolutions(review, self.s.schema, body.resolutions)
        request = ConfirmRequest(
            company_id=cid,
            draft_version=review.draft_version,
            confirmed_by="Automatische Profilerstellung"
            if automatic
            else self.s.settings.advisor_label,
            decisions=decisions,
        )
        if update_dd:
            # Missing fields were not part of this diff; preserve them by removing them
            # from the scoped draft passed to the confirmation validator.
            scoped = review.draft.model_copy(deep=True)
            scoped.fields = [
                f
                for f in scoped.fields
                if f.name in visible or any(x.id.startswith(f"row:{f.name}:") for x in shown.facts)
            ]
            for field in scoped.fields:
                if self.s.schema.field(field.name).is_list:
                    field.items = [
                        i
                        for i in field.items
                        if m.row_fact_id(self.s.schema.field(field.name), i.key) in visible
                    ]
            scoped.schema_fields = [f.name for f in scoped.fields]
            request.draft_version = draft_version(scoped)
            confirmed, _ = apply_decisions(
                scoped, self.s.schema, request, self.s.settings.rounding_max_granularity
            )
            profile = await self.repo.read_ui_profile(cid, self.s.schema, self.s.settings)
            changes = m.update_changes_from_draft(confirmed, self.s.schema, profile, decisions)
            if changes:
                result = await self.repo.update_ui_profile(
                    UpdateRequest(
                        company_id=cid,
                        profile_version=profile.profile_version,
                        changed_by=request.confirmed_by,
                        reason="Aus neuen Dokumenten übernommen",
                        changes=changes,
                    ),
                    self.s.schema,
                    self.s.settings,
                    date.today(),
                )
            else:
                result = None
            await self.remove_working(cid)
            await self.repo.remove_profile_sources(cid)
            if result and result.embeddings_stale:
                await self.refresh(cid, set(result.changed_features))
        else:
            await confirm_profile(self.repo, self.s.schema, self.s.settings, request)
            info = await self.repo.get_client_info(cid) or ClientInfo(company_id=cid)
            info.citations = {}
            await self.repo.save_client_info(info)
            await self.repo.remove_profile_sources(cid)
            try:
                await self.s.jobs.submit_advance(cid)
            except Exception:
                log.exception("advance submission failed; recovery available for %s", cid)
        return await self.get_company(cid)

    async def remove_working(self, cid: str) -> None:
        paths = await cleanup_targets(self.repo, self.s.settings, cid)
        for path in checked_paths(paths, self.s.settings):
            if path.exists():
                await asyncio.to_thread(shutil.rmtree, path)
        await self.repo.delete_working_data(cid)

    async def discard(self, cid: str) -> None:
        await self.idle(cid)
        state, _ = await company(self.s, cid)
        if state and state.state in EMBEDDABLE:
            raise ApiError(
                409, ErrorCode.NOT_ALLOWED, "Bestätigte Mandanten können nicht verworfen werden."
            )
        paths = await cleanup_targets(self.repo, self.s.settings, cid)
        # The repository repeats the state/run checks before deleting any data.
        await self.repo.discard_company(cid)
        for path in checked_paths(paths, self.s.settings):
            if path.exists():
                await asyncio.to_thread(shutil.rmtree, path)

    async def laws(self) -> list[ui.LawChange]:
        return [
            m.law_change(law, await self.repo.list_law_sections(law.id))
            for law in await self.repo.list_laws()
        ]

    async def law(self, law_id: int) -> ui.LawChange:
        return next((x for x in await self.laws() if x.id == str(law_id)), None) or self.missing(
            "Gesetzesänderung"
        )

    @staticmethod
    def missing(label: str):
        raise NotFoundError(f"{label} wurde nicht gefunden.")

    async def matches(self, cid: str | None = None, law_id: int | None = None) -> list[ui.Match]:
        companies = [cid] if cid else [c.id for c in await self.companies()]
        result = []
        for company_id in companies:
            await company(self.s, company_id)
            decisions = await self.repo.list_matches(
                company_id, law_id=law_id, include_history=True
            )
            reviews = {r.law_id: r for r in await self.repo.list_match_reviews(company_id, law_id)}
            ids = {d.law_id for d in decisions if d.judge_relevant} | reviews.keys()
            profile = await self.repo.read_ui_profile(company_id, self.s.schema, self.s.settings)
            codes = {
                r.id: r.values.get("merkmal_code", "") for r in profile.lists.get("merkmal", [])
            }
            for lid in sorted(ids):
                result.append(
                    m.build_match(
                        company_id,
                        lid,
                        [d for d in decisions if d.law_id == lid],
                        reviews.get(lid),
                        codes,
                    )
                )
        return result

    async def match(self, match_id: str) -> ui.Match:
        law_id, cid = m.parse_match_id(match_id)
        return next(
            (x for x in await self.matches(cid, law_id) if x.id == match_id), None
        ) or self.missing("Treffer")

    async def review(self, match_id: str, patch: ui.MatchReviewPatch) -> ui.Match:
        match = await self.match(match_id)
        lid, cid = m.parse_match_id(match_id)
        old = next(
            iter(await self.repo.list_match_reviews(cid, lid)),
            MatchReview(company_id=cid, law_id=lid),
        )
        changes = patch.model_dump(exclude_unset=True, by_alias=False)
        if any(v is None for v in changes.values()):
            raise m.MappingError("Label und Status dürfen nicht leer sein.")
        if (
            old.outreach_id
            and patch.email_status
            and patch.email_status not in {old.email_status, "discussed"}
        ):
            raise ApiError(
                409,
                ErrorCode.STATUS_LOCKED,
                "Gesendete E-Mails können nur als besprochen markiert werden.",
            )
        if (
            patch.email_status == "discussed"
            and old.outreach_id
            and old.email_status == "discussed"
        ):
            changes.pop("email_status", None)
        new = old.model_copy(update=changes)
        if patch.email_status == "sent" and old.email_status != "sent":
            new.sent_at = datetime.now(UTC)
        if patch.email_status == "discussed" and old.email_status != "discussed":
            new.discussed_at = datetime.now(UTC)
        audit = [
            self.audit(
                cid,
                AuditKind.MATCH_REVIEW_CHANGED,
                field=key,
                row_key={"law_id": lid},
                old_value=getattr(old, key),
                new_value=value,
            )
            for key, value in changes.items()
            if getattr(old, key) != value
        ]
        stored = await self.repo.save_match_review(new, audit)
        match.review = m.review_view(stored)
        return match

    def signer(self) -> t.Signer:
        s = self.s.settings
        return t.Signer(name=s.sender_name, title=s.advisor_label, firm=s.sender_firm)

    async def template(self) -> ui.StoredEmailTemplate:
        row = await self.repo.get_template()
        return ui.StoredEmailTemplate(
            subject=row.subject,
            body=row.body,
            is_default=(row.subject, row.body) == (DEFAULT_SUBJECT, DEFAULT_BODY),
            updated_at=m.iso(row.updated_at),
            updated_by=ui.ADVISOR_ID,
        )

    async def client_template(self, cid: str) -> ui.EmailTemplate:
        client, template = await self.get_company(cid), await self.template()
        contact = t.Contact(client.contact.name, client.contact.salutation)
        return ui.EmailTemplate(
            subject=t.fill(template.subject, contact, self.signer()),
            body=t.fill(template.body, contact, self.signer()),
        )

    async def edit_template(
        self, body: ui.TemplateBody, reset: bool = False
    ) -> ui.StoredEmailTemplate:
        laws = [
            t.LawTerms(x.title, tuple(x.tags.tax_types or []), tuple(x.tags.norms or []))
            for x in await self.laws()
        ]
        problems = t.template_problems(body.subject, body.body, laws)
        if problems and not reset:
            code = (
                ErrorCode.LAW_TERMS_FOUND
                if any("gesetzesbezogene" in x for x in problems)
                else ErrorCode.VALIDATION_ERROR
            )
            raise ApiError(422, code, " ".join(problems))
        mail_text(body.subject, body.body, self.s.settings)
        await self.repo.edit_template(body.subject, body.body, reset=reset)
        return await self.template()

    async def template_history(self) -> list[ui.TemplateHistoryEntry]:
        entries = await self.repo.list_audit(AuditFilter(kind=AuditKind.TEMPLATE_EDITED))
        return [
            ui.TemplateHistoryEntry(
                id=str(e.id),
                kind="template_reset" if (e.new_value or {}).get("reset") else "template_edited",
                acted_at=m.iso(e.acted_at),
                old_subject=(e.old_value or {}).get("subject", ""),
                old_body=(e.old_value or {}).get("body", ""),
                new_subject=(e.new_value or {}).get("subject", ""),
                new_body=(e.new_value or {}).get("body", ""),
            )
            for e in entries
        ]

    async def send(self, body: ui.SendEmailBody) -> ui.Outreach:
        client = await self.get_company(body.company_id)
        ids = [m.parse_match_id(x) for x in body.match_ids]
        if any(cid != body.company_id for _, cid in ids) or len(set(ids)) != len(ids):
            raise m.MappingError("Bitte eindeutige Treffer dieses Mandanten auswählen.")
        law_ids = [lid for lid, _ in ids]
        contact = t.Contact(client.contact.name, client.contact.salutation)
        matched = {x.law_change_id for x in await self.matches(body.company_id)}
        laws = [
            t.LawTerms(x.title, tuple(x.tags.tax_types or []), tuple(x.tags.norms or []))
            for x in await self.laws()
            if x.id in matched
        ]
        template = await self.template()
        terms = t.terms_for_client(
            client.name, contact, self.signer(), laws, (template.subject, template.body)
        )
        hits = t.check_email(body.subject, body.body, terms)
        if hits:
            raise ApiError(
                422,
                ErrorCode.LAW_TERMS_FOUND,
                "Gesetzesbezogene Begriffe: " + ", ".join(t.unique_terms(hits)),
            )
        if "{" in body.subject + body.body or "}" in body.subject + body.body:
            raise m.MappingError("Bitte alle Platzhalter vor dem Versand ausfüllen.")
        s = self.s.settings
        address(body.recipient, s.mail_recipient_max_chars)
        mail_text(body.subject, body.body, s)
        destination = s.mail_redirect_to or body.recipient
        address(destination, s.mail_recipient_max_chars)
        subject = (
            f"[TEST an {body.recipient}] {body.subject}" if s.mail_redirect_to else body.subject
        )
        configured = make_provider(s)
        provider = self.s.mail if self.s.mail is not None else configured
        pending = await self.repo.reserve_multi_outreach(
            MultiSendRequest(
                company_id=body.company_id,
                law_ids=law_ids,
                recipient=body.recipient,
                subject=body.subject,
                body=body.body,
                confirmed=body.confirmed,
            ),
            destination,
            subject,
        )
        try:
            message_id = await asyncio.to_thread(provider.send, destination, subject, body.body)
        except DeliveryUncertain:
            raise SendUncertain(
                "Versand unklar. Bitte Gmail prüfen und den Versand gezielt auflösen.", pending.id
            ) from None
        except MailError:
            await self.repo.finish_outreach(
                pending.id, "failed", error="mail provider rejected send"
            )
            raise SendFailed(
                "Versand fehlgeschlagen. Bitte Konfiguration prüfen.", pending.id
            ) from None
        except Exception:
            raise SendUncertain(
                "Versand unklar. Bitte den Versand gezielt auflösen.", pending.id
            ) from None
        sent = await self.repo.finish_outreach(pending.id, "sent", message_id=message_id)
        return m.outreach_view(sent, {})

    async def outreach(self, cid: str) -> list[ui.Outreach]:
        await company(self.s, cid)
        alerts = {a.id: a.law.id for a in await self.repo.list_alerts(cid)}
        return [
            m.outreach_view(o, alerts)
            for o in await self.repo.list_outreach(cid)
            if o.status == "sent"
        ]

    async def reset_demo(self) -> None:
        """Prototype reset (Einstellungen): every client, law, screening result, email and
        audit entry goes; the email template and the vocabulary stay. Source PDFs in
        LAW_SOURCE_DIR are untouched, so the next screening ingests them again."""
        settings = self.s.settings
        if not settings.demo_reset_enabled:
            raise ApiError(403, ErrorCode.NOT_ALLOWED, "Das Zurücksetzen ist deaktiviert.")
        if self.s.runs._tasks or self.s.jobs._tasks:
            raise BusyError("Es laufen noch Verarbeitungen. Bitte warten, bis sie fertig sind.")
        await self.repo.reset_demo_data()
        root = settings.data_dir.resolve()
        for name in (UPLOADS, LAW_UPLOADS):
            path = (root / name).resolve()
            if path != root and path.is_relative_to(root) and path.is_dir():
                await asyncio.to_thread(shutil.rmtree, path, True)

    async def calendar(self) -> list[ui.CalendarEntry]:
        """Every sent email with the client's reply (newest first); meetings come from
        accepted replies."""
        names = {x.id: x.name for x in await self.companies()}
        replies = {r.outreach_id: r for r in await self.repo.list_outreach_responses()}
        return [
            m.calendar_entry(o, names.get(o.company_id, o.company_id), replies.get(o.id))
            for o in await self.repo.list_outreach()
            if o.status == "sent"
        ]

    async def record_response(self, outreach_id: str, body: ui.ResponseBody) -> ui.CalendarEntry:
        oid = int(outreach_id) if outreach_id.isdigit() else 0
        sent = next(
            (o for o in await self.repo.list_outreach() if o.id == oid and o.status == "sent"),
            None,
        ) or self.missing("E-Mail")
        accepted = body.outcome == "accepted"
        if accepted and body.meeting_at is None:
            raise m.MappingError("Bitte Datum und Uhrzeit der Besprechung angeben.")
        if body.received_on < (sent.sent_at or sent.created_at).date():
            raise m.MappingError("Die Antwort kann nicht vor dem Versand eingegangen sein.")
        old = next(
            (
                r
                for r in await self.repo.list_outreach_responses(sent.company_id)
                if r.outreach_id == oid
            ),
            None,
        )
        response = OutreachResponse(
            outreach_id=oid,
            company_id=sent.company_id,
            outcome=body.outcome,
            received_on=body.received_on,
            meeting_at=body.meeting_at.astimezone(UTC) if accepted else None,
            note=(body.note or "").strip() or None,
        )
        snapshot = ("outcome", "received_on", "meeting_at", "note")
        stored = await self.repo.save_outreach_response(
            response,
            [
                self.audit(
                    sent.company_id,
                    AuditKind.RESPONSE_RECORDED,
                    outreach_id=oid,
                    old_value=old.model_dump(mode="json", include=set(snapshot)) if old else None,
                    new_value=response.model_dump(mode="json", include=set(snapshot)),
                )
            ],
        )
        name = next((x.name for x in await self.companies() if x.id == sent.company_id), None)
        return m.calendar_entry(sent, name or sent.company_id, stored)

    async def history(self, cid: str) -> list[ui.AuditEntry]:
        await company(self.s, cid)
        outreach = {o.id: o for o in await self.repo.list_outreach(cid)}
        return [
            value
            for e in await self.repo.list_audit(AuditFilter(company_id=cid))
            if (value := m.audit_view(e, self.s.schema, outreach))
        ]

    async def run(self, run_id: str) -> ui.UploadRun:
        run = await self.repo.get_run(run_id)
        if run is None:
            raise NotFoundError("Verarbeitung wurde nicht gefunden.")
        result = m.run_view(run, await self.repo.list_run_documents(run_id), {})
        if self.s.runs.is_active(run.company_id) and result.status == "finished":
            result.status = "running"  # includes the automatic save before navigation
        return result
