package com.thatcube.hozz

import androidx.activity.compose.setContent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.health.connect.client.HealthConnectClient
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.thatcube.hozz.projection.ProjectionSummary
import com.thatcube.hozz.ui.HozzScreen
import com.thatcube.hozz.ui.HozzTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BetaConsentTest {
    @get:Rule
    val compose = createAndroidComposeRule<HozzTestActivity>()

    @Test
    fun writeRequiresConsentAndCancelDoesNotRequestPermission() {
        verifyConsent(deletions = 0, confirmation = "Continue to write")
    }

    @Test
    fun deletionRequiresPlainWriteAndDeleteConsent() {
        verifyConsent(deletions = 1, confirmation = "Write and delete")
    }

    private fun verifyConsent(deletions: Int, confirmation: String) {
        var requests = 0
        compose.activity.runOnUiThread {
            compose.activity.setContent {
                HozzTheme {
                    HozzScreen(
                        state = HozzUiState(
                            totalRecordCount = 2,
                            healthConnectStatus = HealthConnectClient.SDK_AVAILABLE,
                            projection = ProjectionSummary(insertCount = 1, deleteCount = deletions),
                        ),
                        onImport = {},
                        onExport = {},
                        onWriteHealthConnect = { requests += 1 },
                        onLoadMoreTimeline = {},
                    )
                }
            }
        }
        compose.runOnIdle { assertEquals(0, requests) }
        compose.onNodeWithText("Write mapped records to Health Connect")
            .performScrollTo().performClick()
        compose.onNodeWithText("Apply experimental Health Connect changes?").assertIsDisplayed()
        compose.runOnIdle { assertEquals(0, requests) }
        compose.onNodeWithText("Cancel").performClick()
        compose.runOnIdle { assertEquals(0, requests) }
        compose.onNodeWithText("Write mapped records to Health Connect").performClick()
        compose.onNodeWithText(confirmation).performClick()
        compose.runOnIdle { assertEquals(1, requests) }
    }
}
