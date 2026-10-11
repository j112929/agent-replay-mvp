import { betterAuth } from 'better-auth';
import { organization } from 'better-auth/plugins';
import { APIError } from 'better-auth/api';
import { defaultAc, ownerAc, adminAc, memberAc } from 'better-auth/plugins/organization/access';
import { getMigrations } from 'better-auth/db/migration';
import nodemailer from 'nodemailer';
import { audit } from './db.mjs';

export async function authentication(config, db, entitlements, mailer) {
  const outbox = [];
  const transport = config.smtp ? nodemailer.createTransport({url:config.smtp,requireTLS:config.production,tls:{rejectUnauthorized:true},logger:false,debug:false,connectionTimeout:10000,greetingTimeout:10000,socketTimeout:10000}) : null;
  const send = mailer || (async ({to, subject, text}) => {
    if (transport) await transport.sendMail({from: config.mailFrom, to, subject, text});
    else { outbox.push({to, subject, text}); if (outbox.length > 100) outbox.shift(); }
  });
  const deny = message => { throw new APIError('FORBIDDEN', {message}); };
  function seatCheck(org, pending = false) {
    const limits = entitlements(org).limits;
    const used = db.prepare('SELECT count(*) n FROM member WHERE organizationId=?').get(org).n;
    const reserved = pending ? db.prepare("SELECT count(*) n FROM invitation WHERE organizationId=? AND status='pending' AND expiresAt>?").get(org, Date.now()).n : 0;
    if (used + reserved >= limits.seats) deny('Organization seat limit reached');
  }
  const auth = betterAuth({
    appName: 'Agent Regression Guard', baseURL: config.origin, secret: config.secret,
    database: db, trustedOrigins: [config.origin],
    logger: {disabled: true}, // Auth errors must never log credentials or email links.
    onAPIError: {onError: () => {}},
    emailAndPassword: {enabled: true, minPasswordLength: 12, maxPasswordLength: 128,
      requireEmailVerification: config.production, revokeSessionsOnPasswordReset: true,
      sendResetPassword: async ({user,url}) => send({to:user.email, subject:'Reset your Guard password', text:url})},
    emailVerification: {sendOnSignUp: true, autoSignInAfterVerification: true,
      sendVerificationEmail: async ({user,url}) => send({to:user.email, subject:'Verify your Guard email', text:url})},
    session: {expiresIn: config.sessionDays * 86400, updateAge: 86400, cookieCache: {enabled:false}},
    rateLimit: {enabled: true, storage:'database', window:60, max:60},
    advanced: {useSecureCookies:config.production, ipAddress:{ipAddressHeaders:[]}},
    plugins: [organization({allowUserToCreateOrganization: async user => {
      if (config.production && !user.emailVerified) return false;
      return db.prepare("SELECT count(*) n FROM member WHERE userId=? AND role='owner'").get(user.id).n < 3;
    }, creatorRole:'owner', ac:defaultAc, roles:{owner:ownerAc, admin:adminAc, member:memberAc, viewer:defaultAc.newRole({})},
    invitationExpiresIn:86400 * 7, requireEmailVerificationOnInvitation:config.production,
    sendInvitationEmail: async data => send({to:data.email, subject:`Invitation to ${data.organization.name}`, text:`${config.origin}/debugger.html?invite=${encodeURIComponent(data.id)}`}),
    organizationHooks: {
      beforeCreateOrganization: async ({organization:org}) => {
        if (typeof org.name !== 'string' || org.name.trim().length < 2 || org.name.length > 80 || !/^[a-z0-9][a-z0-9-]{1,59}$/.test(org.slug || '')) deny('Use a name and a lowercase organization slug');
      },
      afterCreateOrganization: async ({organization:org,user}) => {
        const now = Date.now();
        db.prepare('INSERT INTO guard_subscription(org_id,trial_ends,updated_at) VALUES(?,?,?)').run(org.id, now + config.trialDays * 86400000, now);
        audit(db,org.id,user.id,'organization.create');
      },
      beforeCreateInvitation: async ({invitation}) => {
        if (!['admin','member','viewer'].includes(invitation.role)) deny('Invalid invitation role');
        seatCheck(invitation.organizationId, true);
      },
      beforeAcceptInvitation: async ({organization:org}) => seatCheck(org.id),
      beforeAddMember: async ({organization:org,member}) => {
        // The organization plugin adds the founding owner before its create hook.
        if (member.role === 'owner' && !db.prepare('SELECT 1 FROM member WHERE organizationId=?').get(org.id)) return;
        seatCheck(org.id);
      },
      afterAcceptInvitation: async ({organization:org,user}) => audit(db,org.id,user.id,'invitation.accept'),
      afterCreateInvitation: async ({invitation,organization:org}) => audit(db,org.id,invitation.inviterId,'invitation.create',invitation.id),
      afterRemoveMember: async ({member,organization:org}) => {
        db.prepare('UPDATE guard_key SET revoked_at=? WHERE org_id=? AND user_id=?').run(Date.now(),org.id,member.userId);
        db.prepare('DELETE FROM guard_access WHERE user_id=? AND project_id IN (SELECT id FROM guard_project WHERE org_id=?)').run(member.userId,org.id);
        audit(db,org.id,null,'member.access_cleanup',member.userId);
      },
      beforeUpdateMemberRole: async ({newRole}) => {if (!['owner','admin','member','viewer'].includes(newRole)) deny('Choose a supported role');},
      afterUpdateMemberRole: async ({member,organization:org}) => audit(db,org.id,null,'member.role_effect',member.userId)
    }})]
  });
  const migration = await getMigrations(auth.options);
  await migration.runMigrations();
  return {auth, outbox};
}
