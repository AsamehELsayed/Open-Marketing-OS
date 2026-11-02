// Local disposable setup ONLY, using the official image's existing Prisma client.
// The Python provisioning command substitutes synthetic keys into stdin, never argv.
const { PrismaClient } = require('@prisma/client');
const prisma = new PrismaClient();
const crypto = require('crypto');
const input = __DEV050_INPUT__;
(async () => {
  if (input.mode === 'seed') {
    await prisma.$transaction(async tx => {
      for (const suffix of ['a','b']) {
        const org = 'dev050-org-' + suffix;
        await tx.organization.create({data:{id:org,name:'DEV050 synthetic '+suffix,apiKey:input.keys[suffix]}});
        await tx.user.create({data:{id:'dev050-user-'+suffix,email:'dev050-'+suffix+'@example.invalid',providerName:'LOCAL',timezone:0,sendSuccessEmails:false,sendFailureEmails:false,sendStreakEmails:false}});
        await tx.userOrganization.create({data:{id:'dev050-membership-'+suffix,userId:'dev050-user-'+suffix,organizationId:org,role:'USER'}});
        await tx.customer.create({data:{id:'dev050-group-'+suffix,orgId:org,name:'DEV050 group '+suffix}});
      }
    });
  } else if (input.mode === 'channels') {
    for (const suffix of ['a','b']) {
      await prisma.integration.create({data:{id:'dev050-inert-'+suffix,internalId:'dev050-inert-'+suffix,
        organizationId:'dev050-org-'+suffix,name:'DEV050 inert '+suffix,providerIdentifier:'linkedin',
        type:'social',token:'invalid-invalid-invalid',disabled:true,refreshNeeded:false,
        customerId:'dev050-group-'+suffix}});
    }
  } else if (input.mode !== 'snapshot') throw new Error('Invalid provisioning mode');
  const domain = {};
  for (const name of ['integration','post','media']) domain[name] = await prisma[name].findMany({orderBy:{id:'asc'}});
  const domain_fingerprint = crypto.createHash('sha256').update(JSON.stringify(domain)).digest('hex');
  console.log(JSON.stringify({domain_fingerprint,organizations:await prisma.organization.count(),users:await prisma.user.count(),
    groups:await prisma.customer.count(),integrations:await prisma.integration.count(),
    posts:await prisma.post.count(),media:await prisma.media.count()}));
})().catch(() => { console.error('DEV050 local provisioning failed'); process.exitCode=1; })
  .finally(() => prisma.$disconnect());
