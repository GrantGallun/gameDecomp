| functions byte-exact | **347** of 1074 attempted |
| — of which SOLVED | **256** |
| — of which header-assisted (reconstructed include/game) | 11 |
| — of which reference-type-assisted (a type only the target's src/ defines) | 25 |
| — of which recovered from target source | 55 |
| attempts logged | 95,853 |
| evidence rows | 72,845 |
| **inference rows** | **0** |
| tests | 0 |

Additional MATCH TIER, counted separately so each number carries one claim:
| ROM-backed FUNCTION-exact, scope covers the function AND what it reads | **12** |
| — function bytes and external calls only (narrower scope, needs re-certification) | 0 |
| fresh-cohort nodes reconciled INTO the counts above by eval/cohort_reconcile.py | 105 |

  counted:  __osSpRawStartDma, calculateFixedAngleBetweenXZPoints, closeRaceRecordSettingsFlow, drawMainMenuModeSelectMenuOptions, fadeInRaceGameplayViewports, initControllerPakFileDeleteFlow, initMainMenu, initRaceTypeSelectMenu, osSpTaskStartGo, rmonPrintf, updateRaceCameraMenuPreview, updateRacePlayerPostUpdateAttack
  cohort:   __osPiGetAccess, __osSiCreateAccessQueue, __osSiGetAccess, _collectPVoices, alAuxBusNew, alEnvmixerNew, alMainBusNew, alResampleNew, alSynAllocFX, alSynStopVoice, createRaceSetupOpponentFocus, createThrownTrailImpactProjectile, drawCharacterSelectCourseSubmenuFrame, drawMainMenuTitleCursor, drawRaceSetupPlayerCountCursor, drawRaceSplitscreenSelectArrowPrompt, drawRaceSplitscreenSelectCornerSprites, drawRaceTypeSelectArrowPrompt, drawRaceTypeSelectCursor, drawShopMenuPromptPanel, enterMainMenuFromRace, fadeOutControllerPakReplaySaveMessageFlow, fadeOutRaceRecordSettingsFlow, func_80057E10, func_8005AEB0, func_8005C3E4, func_8005C4EC, func_80061A98, func_80063164, func_80065D24, handleRaceTypeSelectMenuSelection, initControllerPakFileDeleteMainOptions, initCourseSelectCourseDescription, initRaceIntroFlyoverLongPanReturn, initRaceIntroFlyoverShortPanFinal, initRaceItemProjectileTrailEffect, initRacePlayerSnowSpray, initRacePlayers, initRaceSetupSavePanelIcons, initRaceSplitscreenSelectOption3Frame, initRaceUiItemStealTrailEffect, initShopMenuCourseListPanel, initThrownPickupModel, initThrownTrailImpactProjectile, initTrainingCourseEndingDialog, openEndingCreditsIfUnlockedFlow, openRaceTypeSelectFlow, openStartupReplaySaveMessageFlow, osSpTaskYielded, osViSwapBuffer, osVirtualToPhysical, requestControllerPakSaveRead, returnToRaceTypeSelectMenu, routeRaceCharacterSetupFlow, spawnPatrolCourseObject, spawnRaceUiAltBurstTextParticle, startEndingSlashHandshakeLoop, startEndingSlashVanishBeforeExitRight, startRaceGameplayFlow, updateControllerPakFileDeleteFileListUi, updateControllerPakReplaySaveMessageFirstPageFadeIn, updateCourseGateClosing, updateCourseTriggerVolume, updateEndingCreditsTheEndTextFadeIn, updateEndingCreditsTransitionSnowflakeIconForwardSpin, updateEndingCreditsTumblingSnowboardWaitForRemove, updateEndingJamHandshakeLoopSecond, updateEndingJamPhase3DPrep, updateEndingJamPhase3FAnim3, updateEndingLindaAfterIntroAnim1, updateEndingSlashSlideLeftUntilPhase18, updateFinalLapPrompt, updateLaunchRampCourseObjectExit, updateMainMenuModeBoardAfterimage, updateMainMenuModeDescriptionPanel, updateMenuSpriteActorDebugControls, updateRaceFlowFrame, updateRaceIntroFlyoverIdle, updateRaceIntroFlyoverLongPanHold, updateRaceIntroFlyoverShortPanFinal, updateRacePlayerMode29Crash, updateRacePlayerMode32Character1, updateRacePlayerMode32Character2, updateRacePlayerMode32Character3, updateRacePlayerMode32Character5, updateRacePlayerPostUpdateMode22, updateRaceReplayFrame, updateRaceSetupCornerPrompts, updateRaceUiPrizePayoutShowRankPrize, updateRaceUiTrickPrizePayoutRevealMoneyRow, updateRaceUiTrickPrizePayoutRevealTrickPrize, updateSpiralCourseObjectLaunch, updateTitleScreenStartPrompt, waitEndingJamPhase13, waitEndingJamPhase2F, waitEndingJamPhase40, waitEndingLindaPhase31, waitEndingLindaPhase38, waitEndingNancyPhase29, waitEndingNancyPhase36, waitEndingTommyPhase0B, waitEndingTommyPhase0F, waitForMainMenuModePreviewRaceStart, waitForTrainingCourseLessonEndMenuSelection, waitStartupRumbleInit

(A function-exact function's own words, with relocations resolved, equal the ROM's bytes at
 that address -- behaviour-identical by construction. Object-section exactness is a different
 claim and cannot be satisfied by a single-function candidate.)

(1,297 historical compiled attempt(s) predate persisted exact verdicts. They are treated as unknown, never inferred from score.)
