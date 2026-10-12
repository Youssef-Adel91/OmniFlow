# Facebook / Instagram comment replies

**Status: OFF by default.** Before this change comment replies were always on for any connected Page; they are now off unless BOTH switches are on:
1. platform: `FEATURE_SOCIAL_COMMENT_REPLIES=true` (env), and
2. tenant: Settings → Channels → "الرد على التعليقات" (admin), stored in `tenant_preferences.social_comments` (`enabled`, `mode`).

## What happens to a comment
`POST /api/v1/webhooks/meta` (signed) → `comment_policy.decide()` → ignore, or queue an AI reply:
| Check | Result |
|---|---|
| platform flag off / tenant switch off | ignored |
| comment authored by the tenant's own Page or Instagram account | ignored (our own reply comes back as a webhook; answering it would loop) |
| reply inside a comment thread (IG `parent_id`; FB `parent_id` ≠ post) | ignored |
| empty text | ignored |
| same `comment_id` seen in the last 7 days (Redis counter) | ignored — dedupe. If Redis is down: ignored (cannot prove it is not a duplicate) |
| more than `SOCIAL_COMMENT_RATE_PER_MINUTE` (30) comment replies this minute for the tenant | ignored |
| otherwise | `mode=public_reply`: reply under the comment (FB `POST /{id}/comments`, IG `POST /{id}/replies`); `mode=private_dm`: Messenger private reply (`POST /{page-id}/messages`, `recipient.comment_id`) |

The inbox message shows a first line with the post: Facebook → a link (`https://www.facebook.com/{post_id}`); Instagram → the media id only (a permalink would need an extra Graph call). The AI sees the comment text only.

## Not verified
Everything above is tested with locally produced signed payloads and mocked Graph calls. NOT verified against live Meta: the `private_dm` endpoint/permissions (`pages_messaging` + the Page's Instagram messaging access), the 7-day/one-per-comment limits, and Facebook permalink formats. Test on a real test Page before turning the flag on.
