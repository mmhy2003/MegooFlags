package io.github.mmhy2003.megooflags.internal;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Path;
import javax.xml.parsers.DocumentBuilderFactory;
import org.junit.jupiter.api.Test;
import org.w3c.dom.Element;
import org.w3c.dom.Node;

class BuildInfoTest {
    @Test
    void versionIsZeroOneZero() {
        assertEquals("0.1.0", BuildInfo.VERSION);
    }

    @Test
    void versionMatchesPom() throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        Element project =
                factory.newDocumentBuilder().parse(Path.of("pom.xml").toFile()).getDocumentElement();
        String version = null;
        for (Node n = project.getFirstChild(); n != null; n = n.getNextSibling()) {
            if (n instanceof Element && "version".equals(n.getNodeName())) {
                version = n.getTextContent().trim();
            }
        }
        assertEquals(version, BuildInfo.VERSION);
    }
}
