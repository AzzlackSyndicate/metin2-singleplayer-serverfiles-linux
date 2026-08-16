-- "Third Hand" (auto-pickup of Yang) for every character, for ten years.
--
-- WHAT THIS IS. The passive shown on the top left that vacuums up the Yang a
-- monster drops, straight into the purse, without a click. In this server's
-- core it is not an item effect and not a quest affect -- it is the account
-- premium PREMIUM_AUTOLOOT. char_battle.cpp, CHARACTER::RewardGold:
--
--     bool isAutoLoot =
--         (pkAttacker->GetPremiumRemainSeconds(PREMIUM_AUTOLOOT) > 0 ||
--          pkAttacker->IsEquipUniqueGroup(UNIQUE_GROUP_AUTOLOOT))       // 제3의 손
--
-- and GetPremiumRemainSeconds reads m_aiPremiumTimes[], which the db core loads
-- at login from ONE column: account.account.autoloot_expire (db.cpp, the
-- AUTH_LOGIN query, "UNIX_TIMESTAMP(autoloot_expire)"). So the whole feature is
-- that column holding a date in the future. The client shows the top-left
-- premium icon from the same login data, so setting the date turns on both the
-- pickup and its badge.
--
-- WHY SQL AND NOT A QUEST. There is no quest binding that SETS premium time --
-- questlua_pc.cpp exposes only pc.get_premium_remain_sec, the reader. The value
-- lives in the database, so the database is where it is granted.
--
-- WHY THIS IS SAFE TO RUN AGAIN. Staged into the panel's schema directory, whose
-- entrypoint applies every .sql there on EVERY start -- the one mechanism in
-- this project that reaches an existing database and keeps reaching it. The
-- WHERE clause only ever RAISES a date that is missing or under nine years out,
-- so replaying it does nothing to an account that already has the grant, and it
-- can never shorten one that some other means (a real premium purchase) has set
-- longer. New accounts, which start with no future date here, are picked up on
-- the next panel start.
--
-- TO TAKE IT BACK OUT: stop staging this file, then clear the dates deliberately
-- -- the schema mechanism never deletes on an operator's behalf:
--
--     UPDATE account.account SET autoloot_expire = NULL
--      WHERE autoloot_expire > DATE_ADD(NOW(), INTERVAL 9 YEAR);

UPDATE account.account
   SET autoloot_expire = DATE_ADD(NOW(), INTERVAL 10 YEAR)
 WHERE autoloot_expire IS NULL
    OR autoloot_expire < DATE_ADD(NOW(), INTERVAL 9 YEAR);
