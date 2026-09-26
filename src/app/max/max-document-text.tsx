import { documentBlocks } from "@/lib/document-text";
import styles from "./max.module.css";

export function MaxDocumentText({ text }: { text: string }) {
  return (
    <div className={styles.documentText}>
      {documentBlocks(text).map((block, index) => {
        if (block.type === "heading") return <h6 key={index}>{block.text}</h6>;
        if (block.type === "code") return <pre key={index}>{block.text}</pre>;
        if (block.type === "list") {
          const List = block.ordered ? "ol" : "ul";
          return (
            <List key={index}>
              {block.items.map((item, position) => (
                <li key={position}>{item}</li>
              ))}
            </List>
          );
        }
        return <p key={index}>{block.text}</p>;
      })}
    </div>
  );
}
