package com.thatcube.hozz.core

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.thatcube.hozz.projection.ProjectionPlanner
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BetaTransferAcceptanceTest {
    @Test
    fun syntheticTransferReplaysTombstonesAndRoundTripsWithoutProjection() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val prefix = "beta-acceptance-${UUID.randomUUID()}"
        val sourceName = "$prefix-source.sqlite"
        val targetName = "$prefix-target.sqlite"
        val source = SqliteCanonicalRecordStore(context, sourceName)
        val target = SqliteCanonicalRecordStore(context, targetName)
        try {
            val fixture = InstrumentationRegistry.getInstrumentation().context.assets
                .open("hozz/v1/fixtures/canonical-records.ndjson").use { it.readBytes() }
            val importer = ArchiveImporter(source, batchSize = 1)
            assertEquals(3, importer.import(ByteArrayInputStream(fixture)).merge.inserted)
            assertEquals(3, importer.import(ByteArrayInputStream(fixture)).merge.ignored)
            val plan = ProjectionPlanner.plan(source.allRecords())
            assertEquals(1, plan.exactCount)
            assertEquals(2, plan.archiveOnlyCount)
            val ids = source.allRecords().mapTo(mutableSetOf()) { it.canonicalId }
            assertTrue(source.healthConnectProjections(ids).isEmpty())
            assertTrue(source.pendingHealthConnectOperations(ids).isEmpty())

            val weightId = "00000000-0000-0000-0000-000000000001"
            val deletion = """
                {"canonicalId":"apple.healthkit:$weightId","canonicalType":"body.weight","id":"$weightId","kind":"deletion","recordVersion":2,"schemaVersion":1,"sourceRecord":{"id":"$weightId","store":"apple.healthkit","type":"HKQuantityTypeIdentifierBodyMass"},"type":"HKQuantityTypeIdentifierBodyMass"}
            """.trimIndent().toByteArray()
            assertEquals(1, importer.import(ByteArrayInputStream(deletion)).merge.updated)
            assertEquals(1, importer.import(ByteArrayInputStream(deletion)).merge.ignored)
            assertEquals(3, importer.import(ByteArrayInputStream(fixture)).merge.ignored)

            val first = ByteArrayOutputStream()
            val second = ByteArrayOutputStream()
            assertEquals(3, CanonicalArchiveExporter(source).export(first).recordCount)
            CanonicalArchiveExporter(source).export(second)
            assertTrue(first.toByteArray().contentEquals(second.toByteArray()))
            val imported = ArchiveImporter(target).import(ByteArrayInputStream(first.toByteArray()))
            assertFalse(imported.legacyArchive)
            assertEquals(3, imported.merge.inserted)
            assertEquals(
                3,
                ArchiveImporter(target).import(ByteArrayInputStream(first.toByteArray())).merge.ignored,
            )
            val sourceRecords = source.allRecords().associateBy { it.canonicalId }
            val targetRecords = target.allRecords().associateBy { it.canonicalId }
            assertEquals(sourceRecords.keys, targetRecords.keys)
            sourceRecords.forEach { (id, record) ->
                assertEquals(
                    CanonicalArchiveExporter(source).canonicalJson(record),
                    CanonicalArchiveExporter(target).canonicalJson(targetRecords.getValue(id)),
                )
                assertEquals(record.lineage, targetRecords.getValue(id).lineage)
            }
            assertEquals(1, targetRecords.values.count { it.tombstone })
            assertEquals(2, target.timeline().size)
            assertTrue(target.healthConnectProjections(ids).isEmpty())
            assertTrue(target.pendingHealthConnectOperations(ids).isEmpty())
        } finally {
            source.close()
            target.close()
            context.deleteDatabase(sourceName)
            context.deleteDatabase(targetName)
        }
    }
}
